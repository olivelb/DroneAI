import os
import json
import math
import shutil
import subprocess
import sys
import threading
import time
import logging
import numpy as np
from PIL import Image as PILImage
from PIL.ExifTags import GPSTAGS
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_CONTROL, TOPIC_MISSION, TOPIC_ORTHO, TOPIC_STATUS
from shared import storage
from shared.geospatial_assets import convert_to_cog, metadata_path, preview_path
from shared.pipeline_params import (
    normalize_feature_type,
    normalize_matcher_type,
)
from shared.dronegs_profile import DRONEGS_PRODUCTION_PROFILE_V1
from shared.rtk_refinement import (
    inject_database_gravity_priors,
    inject_database_pose_priors,
    load_rtk_records,
)
from shared.gcp_control import (
    build_weighted_gcp_alignment,
    prepare_gcp_assets,
    write_transformed_reconstruction,
)
from pipeline_support import (
    build_colmap_cache_config,
    choose_dronegs_data_factor,
    changed_colmap_cache_parameters,
    discover_input_assets,
    load_copy_manifest,
    load_colmap_cache_config,
    detect_existing_pipeline,
    extract_gps_data,
    inspect_sparse_reconstruction,
    inspect_sparse_quality,
    is_aliked_feature_type,
    merge_pipeline_params,
    normalize_ai_backend,
    read_saved_projected_crs,
    read_saved_projected_crs_policy,
    resolve_feature_family,
    resolve_feature_matching_type,
    sanitize_exif_for_colmap,
    save_projected_crs,
    save_copy_manifest,
    save_colmap_cache_config,
    plan_clean_image_copy,
)
from runtime_support import run_command
from alignment_support import (
    atomic_write_json,
    build_gps_pair_graph,
    build_mapping_command,
    caspar_compatibility,
    choose_auto_fallback,
    database_counts,
    parse_colmap_reference_file,
    write_pair_list,
)
from worker_support import (
    MissionStateTracker,
    WorkerCancellationState,
    build_mission_context,
    control_consumer_loop,
    create_consumer,
    create_producer,
    log_mission_start,
    make_progress_reporter,
    publish_next_stage_message,
)
from shared.config import TOPIC_DEAD_LETTER
from shared.kafka_reliability import process_message

# --- CONFIGURATION KAFKA ---
TOPIC_IN = TOPIC_MISSION
TOPIC_OUT = TOPIC_ORTHO

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app1-colmap")

cancellation_state = WorkerCancellationState()
mission_state_tracker = MissionStateTracker()

class PipelineCancelledError(Exception):
    pass

def control_consumer_thread():
    control_consumer_loop(
        KAFKA_BROKER,
        TOPIC_CONTROL,
        cancellation_state.should_cancel,
        cancellation_state.on_cancel,
        logger,
        producer,
        TOPIC_DEAD_LETTER,
    )


producer = create_producer(KAFKA_BROKER)
report_progress = make_progress_reporter(producer, TOPIC_STATUS, service_name="COLMAP")


def report_mission_progress(vol_id, step, progress, status="processing", log=None, details=None):
    mission_state_tracker.record_progress(vol_id, step, progress, status=status, log=log, details=details)
    report_progress(vol_id, step, progress, status=status, log=log, details=details)


def ensure_not_cancelled(process=None):
    try:
        cancellation_state.ensure_not_cancelled(process)
    except RuntimeError as error:
        raise PipelineCancelledError(str(error)) from error


def invalidate_pipeline_artifacts(clean_images_dir, workspace_dir, db_path, sparse_path, dense_path, geo_data_file, vol_id, reason):
    artifact_paths = [
        db_path,
        f"{db_path}-shm",
        f"{db_path}-wal",
        sparse_path,
        os.path.join(workspace_dir, "sparse_rtk"),
        dense_path,
        os.path.join(workspace_dir, "sparse_geo"),
        os.path.join(workspace_dir, "alignment_transform.json"),
        os.path.join(workspace_dir, "orthomosaic.tif"),
        os.path.join(workspace_dir, "gps_pairs.txt"),
        os.path.join(workspace_dir, "alignment_pair_graph.json"),
        os.path.join(workspace_dir, "rtk_prior_report.json"),
        os.path.join(workspace_dir, "imu_gravity_report.json"),
        os.path.join(workspace_dir, "gcp_alignment_report.json"),
        os.path.join(workspace_dir, ".colmap_pipeline_config.json"),
        geo_data_file,
        f"{geo_data_file}.crs",
        f"{geo_data_file}.crs.json",
        os.path.join(clean_images_dir, ".colmap_exif_sanitized"),
    ]

    removed_paths = []
    for path in artifact_paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed_paths.append(path)
        elif os.path.exists(path):
            os.remove(path)
            removed_paths.append(path)

    if removed_paths:
        report_mission_progress(
            vol_id,
            "PREPARING",
            3,
            log=f"{reason} Removed {len(removed_paths)} stale pipeline artifacts.",
        )

    return removed_paths


def invalidate_georeferencing_artifacts(workspace_dir, geo_data_file, vol_id, reason):
    artifact_paths = [
        os.path.join(workspace_dir, "sparse_geo"),
        os.path.join(workspace_dir, "alignment_transform.json"),
        os.path.join(workspace_dir, "orthomosaic.tif"),
        os.path.join(workspace_dir, "orthomosaic.height.tif"),
        os.path.join(workspace_dir, "gcp_alignment_report.json"),
        geo_data_file,
        f"{geo_data_file}.crs",
        f"{geo_data_file}.crs.json",
    ]
    removed_paths = []
    for path in artifact_paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed_paths.append(path)
        elif os.path.exists(path):
            os.remove(path)
            removed_paths.append(path)
    if removed_paths:
        report_mission_progress(
            vol_id,
            "PREPARING",
            3,
            log=f"{reason} Removed {len(removed_paths)} stale georeferencing artifacts.",
        )
    return removed_paths


def normalize_gpu_index(raw_value, default="0"):
    normalized = str(raw_value if raw_value is not None else default).strip()
    if not normalized or normalized == "-1":
        normalized = default

    visible_devices = [
        token.strip()
        for token in os.getenv("CUDA_VISIBLE_DEVICES", "").split(",")
        if token.strip()
    ]
    if len(visible_devices) == 1:
        return "0"
    if normalized.isdigit() and visible_devices and int(normalized) >= len(visible_devices):
        return "0"
    return normalized


def dense_sparse_model_ready(dense_path):
    sparse_dir = os.path.join(dense_path, "sparse")
    return (
        os.path.exists(os.path.join(sparse_dir, "cameras.bin"))
        and os.path.exists(os.path.join(sparse_dir, "images.bin"))
        and os.path.exists(os.path.join(sparse_dir, "points3D.bin"))
    )


def run_colmap_pipeline(workspace_dir, input_dataset, vol_id, mission_params):
    durable_checkpoint_dir = None
    gaussian_upload_complete = False
    try:
        # --- Pipeline selection ---
        pipeline_mode = mission_params.get("pipeline", "modern")
        if pipeline_mode not in ("modern", "legacy"):
            report_mission_progress(vol_id, "WARNING", 1, log=f"Unknown pipeline '{pipeline_mode}', defaulting to 'modern'")
            pipeline_mode = "modern"
        
        params = merge_pipeline_params(pipeline_mode, mission_params)
        feature_type = normalize_feature_type(params.get("feature_type"))
        matcher_type = normalize_matcher_type(params.get("matcher_type"))
        feature_family = resolve_feature_family(feature_type)
        resolved_matcher_type = resolve_feature_matching_type(feature_type, matcher_type)
        feature_gpu_index = normalize_gpu_index(
            os.getenv("ALIKED_GPU_INDEX", "0") if feature_family == "ALIKED" else os.getenv("SIFT_GPU_INDEX", "0")
        )
        ba_gpu_index = normalize_gpu_index(os.getenv("COLMAP_BA_GPU_INDEX", "0"))
        params["feature_type"] = feature_type
        params["matcher_type"] = matcher_type
        projected_crs_mode = str(
            params.get("projected_crs_mode", "auto-local")
        ).strip().lower()
        requested_projected_crs = str(params.get("projected_crs", "")).strip()
        report_mission_progress(
            vol_id,
            "PIPELINE",
            1,
            log=(
                f"Using {'🚀 COLMAP 4 Modern defaults' if pipeline_mode == 'modern' else '🔧 Legacy defaults'} "
                f"with extractor={feature_type} and matcher={matcher_type} ({resolved_matcher_type})."
            ),
        )
        
        # --- S3 prefix for this mission ---
        mission_s3_prefix = f"missions/{vol_id}"

        # --- 1. Preparation ---
        report_mission_progress(vol_id, "PREPARING", 2, log=f"Creating workspace at {workspace_dir}")
        os.makedirs(workspace_dir, exist_ok=True)
        
        raw_image_dir = os.path.join(workspace_dir, "raw_images")
        os.makedirs(raw_image_dir, exist_ok=True)
        clean_images_dir = os.path.join(workspace_dir, "clean_images")
        os.makedirs(clean_images_dir, exist_ok=True)

        # Download input images from S3
        # Normalize prefix: strip trailing slashes to avoid double-slash S3 keys
        input_dataset = input_dataset.rstrip("/")
        report_mission_progress(vol_id, "DOWNLOADING_IMAGES", 3, log=f"Downloading input images from S3 prefix: {input_dataset}")
        try:
            n_downloaded = storage.download_directory(input_dataset + "/", raw_image_dir)
            if n_downloaded == 0:
                # Try without trailing slash
                n_downloaded = storage.download_directory(input_dataset, raw_image_dir)
            report_mission_progress(vol_id, "DOWNLOADING_IMAGES", 5, log=f"Downloaded {n_downloaded} files from S3")
        except Exception as dl_err:
            raise RuntimeError(f"Failed to download input dataset from S3: {input_dataset} — {dl_err}") from dl_err
        db_path = os.path.join(workspace_dir, "database.db")
        sparse_path = os.path.join(workspace_dir, "sparse")
        geo_data_file = os.path.join(workspace_dir, "geo_data.txt")
        dense_path = os.path.join(workspace_dir, "dense")
        gcp_assets = prepare_gcp_assets(raw_image_dir, workspace_dir)
        gcp_path = gcp_assets["gcp_path"]
        gcp_accuracy_path = gcp_assets["accuracy_path"]
        if gcp_path:
            report_mission_progress(
                vol_id,
                "PREPARING",
                5,
                log=(
                    "Prepared surveyed GCP observations"
                    + (
                        " with per-point covariance and roles."
                        if gcp_accuracy_path
                        else " with mission-level default accuracy."
                    )
                ),
                details={
                    "event": "gcp_assets_prepared",
                    "accuracy_file": bool(gcp_accuracy_path),
                    "changed": bool(gcp_assets["changed"]),
                },
            )
        
        images, position_sidecars = discover_input_assets(raw_image_dir)
        copy_candidates = images + position_sidecars
        report_mission_progress(
            vol_id,
            "COPYING_IMAGES",
            5,
            log=(
                f"Copying {len(images)} images and {len(position_sidecars)} "
                "DJI position sidecars to the clean workspace..."
            ),
        )
        
        copied_count = 0
        skipped_count = 0
        copy_manifest = load_copy_manifest(clean_images_dir)
        for i, input_path in enumerate(copy_candidates):
            try:
                cancellation_state.ensure_not_cancelled()
            except RuntimeError as error:
                raise PipelineCancelledError(str(error)) from error

            asset_name = input_path.name
            src_path = str(input_path)
            dst_path = os.path.join(clean_images_dir, asset_name)
            needs_copy, source_descriptor = plan_clean_image_copy(
                src_path,
                dst_path,
                copy_manifest.get(asset_name),
            )

            if not needs_copy:
                skipped_count += 1
            else:
                shutil.copy2(src_path, dst_path)
                copied_count += 1
                if source_descriptor is None:
                    needs_copy, source_descriptor = plan_clean_image_copy(
                        src_path,
                        dst_path,
                        copy_manifest.get(asset_name),
                    )

            if source_descriptor is not None:
                copy_manifest[asset_name] = source_descriptor

            if (i + 1) % 50 == 0 or i == len(copy_candidates) - 1:
                save_copy_manifest(clean_images_dir, copy_manifest)
                
                report_mission_progress(
                    vol_id,
                    "COPYING_IMAGES",
                    5,
                    log=(
                        f"Processed {i + 1}/{len(copy_candidates)} input files "
                        f"(Copied: {copied_count}, Skipped: {skipped_count})"
                    ),
                    details={
                        "event": "copy_progress",
                        "processed": i + 1,
                        "total": len(copy_candidates),
                        "copied": copied_count,
                        "skipped": skipped_count,
                    },
                )

        # Free disk: remove raw_images now that clean_images is ready.
        # On re-run, images will be re-downloaded from S3 (fast over local network).
        if os.path.isdir(raw_image_dir):
            shutil.rmtree(raw_image_dir)
            report_mission_progress(vol_id, "COPYING_IMAGES", 5, log="Removed raw_images to free disk space")

        if copied_count > 0:
            invalidate_pipeline_artifacts(
                clean_images_dir,
                workspace_dir,
                db_path,
                sparse_path,
                dense_path,
                geo_data_file,
                vol_id,
                "Input images changed since the last cached run.",
            )

        requested_cache_config = build_colmap_cache_config(params)
        previous_cache_config = load_colmap_cache_config(workspace_dir)
        has_cached_reconstruction = any(
            os.path.exists(path)
            for path in (db_path, sparse_path, dense_path)
        )
        if (
            has_cached_reconstruction
            and (
                previous_cache_config is None
                or previous_cache_config.get("fingerprint")
                != requested_cache_config["fingerprint"]
            )
        ):
            changed_parameters = changed_colmap_cache_parameters(
                previous_cache_config,
                requested_cache_config,
            )
            invalidate_pipeline_artifacts(
                clean_images_dir,
                workspace_dir,
                db_path,
                sparse_path,
                dense_path,
                geo_data_file,
                vol_id,
                "COLMAP reconstruction parameters changed "
                f"({', '.join(changed_parameters)}).",
            )

        image_reader_camera_model = str(params.get("camera_model", "SIMPLE_RADIAL")).upper()
        image_reader_camera_params = None
        
        # --- Smart resume: check database descriptor type compatibility ---
        existing_type = detect_existing_pipeline(db_path)
        
        if existing_type is not None:
            # Database exists from a previous run
            if feature_family == "ALIKED" and existing_type == "SIFT":
                invalidate_pipeline_artifacts(
                    clean_images_dir,
                    workspace_dir,
                    db_path,
                    sparse_path,
                    dense_path,
                    geo_data_file,
                    vol_id,
                    f"Existing workspace uses SIFT features but extractor {feature_type} was requested.",
                )
            elif feature_family == "SIFT" and existing_type == "ALIKED":
                invalidate_pipeline_artifacts(
                    clean_images_dir,
                    workspace_dir,
                    db_path,
                    sparse_path,
                    dense_path,
                    geo_data_file,
                    vol_id,
                    f"Existing workspace uses ALIKED features but extractor {feature_type} was requested.",
                )
            else:
                report_mission_progress(vol_id, "PREPARING", 3, log=f"Existing database compatible ({existing_type}). Resuming...")

        save_colmap_cache_config(workspace_dir, requested_cache_config)
        
        # --- 2. GPS ---
        saved_projected_crs = read_saved_projected_crs(geo_data_file)
        saved_projection_policy = read_saved_projected_crs_policy(geo_data_file)
        gps_done = (
            os.path.exists(geo_data_file)
            and os.path.getsize(geo_data_file) > 0
            and bool(saved_projected_crs)
        )
        projection_changed = False
        if gps_done and not saved_projection_policy:
            projection_changed = True
        elif gps_done and saved_projection_policy.get("policy") != projected_crs_mode:
            projection_changed = True
        elif gps_done and projected_crs_mode == "custom":
            projection_changed = (
                saved_projected_crs.upper() != requested_projected_crs.upper()
            )
        if projection_changed or (
            os.path.exists(geo_data_file) and not saved_projected_crs
        ):
            invalidate_georeferencing_artifacts(
                workspace_dir,
                geo_data_file,
                vol_id,
                "The requested projected CRS policy changed.",
            )
            gps_done = False

        if gps_done:
            utm_crs = saved_projected_crs
            report_mission_progress(
                vol_id,
                "GPS_EXTRACTION",
                12,
                log=f"Existing projected GPS references found. Reusing CRS {utm_crs}.",
            )
        else:
            utm_crs = extract_gps_data(
                clean_images_dir,
                geo_data_file,
                vol_id,
                report_mission_progress,
                projected_crs_mode=projected_crs_mode,
                projected_crs=requested_projected_crs,
            )
            save_projected_crs(
                geo_data_file,
                utm_crs,
                policy=projected_crs_mode,
                requested_crs=requested_projected_crs,
            )

        gps_done = os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0
        sanitize_exif_for_colmap(clean_images_dir, vol_id, report_mission_progress)

        align_tf = os.path.join(workspace_dir, "alignment_transform.json")
        align_tf = align_tf if os.path.exists(align_tf) else None
        dense_sparse_ready = dense_sparse_model_ready(dense_path)

        # Gaussian Splatting only needs dense/sparse + undistorted images.
        gs_ready = dense_sparse_ready and os.path.isdir(os.path.join(dense_path, "images"))
        ortho_only_ready = gs_ready
        if ortho_only_ready:
            report_mission_progress(vol_id, "PREPARING", 13, log="Existing undistorted images found. Skipping SfM and rebuilding Gaussian Splatting orthomosaic only.")

        # --- 3. SfM: Feature Extraction ---
        sparse_done = os.path.exists(os.path.join(sparse_path, "0", "cameras.bin")) or os.path.exists(os.path.join(sparse_path, "0", "cameras.txt"))

        if not sparse_done and not ortho_only_ready:
            # Build feature extraction command based on the selected extractor.
            feature_num_threads = str(params.get("feature_num_threads", "-1"))
            feat_cmd = [
                "colmap", "feature_extractor",
                "--database_path", db_path,
                "--image_path", clean_images_dir,
                "--ImageReader.single_camera", "1",
                "--ImageReader.camera_model", image_reader_camera_model,
                "--FeatureExtraction.num_threads", feature_num_threads,
            ]
            if image_reader_camera_params:
                feat_cmd += [
                    "--ImageReader.camera_params", image_reader_camera_params,
                ]
            
            if feature_family == "ALIKED":
                requested_feature_max_image_size = int(float(params["feature_max_image_size"]))
                safe_aliked_max_image_size = int(float(os.getenv("ALIKED_SAFE_MAX_IMAGE_SIZE", "1600")))
                effective_feature_max_image_size = min(
                    requested_feature_max_image_size,
                    safe_aliked_max_image_size,
                )
                if effective_feature_max_image_size < requested_feature_max_image_size:
                    report_mission_progress(
                        vol_id,
                        "FEATURES",
                        14,
                        log=(
                            f"Clamping ALIKED extraction size from {requested_feature_max_image_size}px to "
                            f"{effective_feature_max_image_size}px to prevent ONNX CUDA memory blow-ups."
                        ),
                    )
                feat_cmd += [
                    "--FeatureExtraction.type", params["feature_type"],
                    "--FeatureExtraction.use_gpu", "1",
                    "--FeatureExtraction.gpu_index", feature_gpu_index,
                    "--FeatureExtraction.max_image_size", str(effective_feature_max_image_size),
                    "--AlikedExtraction.max_num_features", params["feature_max_num_features"],
                ]
                model_dir = os.getenv(
                    "COLMAP_MODEL_DIR",
                    "/usr/local/share/colmap/models",
                )
                model_filename = (
                    "aliked-n32.onnx"
                    if params["feature_type"] == "ALIKED_N32"
                    else "aliked-n16rot.onnx"
                )
                model_option = (
                    "--AlikedExtraction.n32_model_path"
                    if params["feature_type"] == "ALIKED_N32"
                    else "--AlikedExtraction.n16rot_model_path"
                )
                feat_cmd += [model_option, os.path.join(model_dir, model_filename)]
            else:
                feat_cmd += [
                    "--FeatureExtraction.type", feature_type,
                    "--FeatureExtraction.use_gpu", "1",
                    "--FeatureExtraction.gpu_index", feature_gpu_index,
                    "--FeatureExtraction.max_image_size", params["feature_max_image_size"],
                    "--SiftExtraction.max_num_features", params["feature_max_num_features"],
                    "--SiftExtraction.first_octave", str(params["sift_first_octave"]),
                ]
            
            run_command(feat_cmd, vol_id, "FEATURES", 15, report_mission_progress, ensure_not_cancelled)
            
            # --- 4. SfM: bounded Feature Matching ---
            matching_strategy = str(params.get("matching_strategy", "gps_pairs")).lower()
            model_dir = os.getenv(
                "COLMAP_MODEL_DIR",
                "/usr/local/share/colmap/models",
            )
            matching_model_options = []
            if resolved_matcher_type == "ALIKED_LIGHTGLUE":
                matching_model_options = [
                    "--AlikedMatching.lightglue_model_path",
                    os.path.join(model_dir, "aliked-lightglue.onnx"),
                ]
            elif resolved_matcher_type == "SIFT_LIGHTGLUE":
                matching_model_options = [
                    "--SiftMatching.lightglue_model_path",
                    os.path.join(model_dir, "sift-lightglue.onnx"),
                ]
            pair_graph_stats = None
            if matching_strategy == "gps_pairs" and gps_done:
                positioned = parse_colmap_reference_file(geo_data_file)
                pairs, pair_graph_stats = build_gps_pair_graph(
                    positioned,
                    max_neighbors=int(float(params["gps_pair_max_neighbors"])),
                    min_neighbors=int(float(params["gps_pair_min_neighbors"])),
                    temporal_neighbors=int(float(params["gps_pair_temporal_neighbors"])),
                    max_distance_m=float(params["gps_pair_max_distance_m"]),
                )
                pair_list_path = os.path.join(workspace_dir, "gps_pairs.txt")
                pair_count = write_pair_list(pair_list_path, pairs)
                atomic_write_json(
                    os.path.join(workspace_dir, "alignment_pair_graph.json"),
                    pair_graph_stats,
                )
                if pair_count == 0:
                    raise RuntimeError(
                        "GPS pair selection produced no pairs. Check EXIF positions "
                        "or choose the spatial/sequential matching strategy."
                    )
                report_mission_progress(
                    vol_id,
                    "MATCHING",
                    25,
                    log=(
                        f"Matching {pair_count} bounded GPS/temporal pairs for "
                        f"{pair_graph_stats['positioned_images']} positioned images "
                        f"(mean degree {pair_graph_stats['mean_degree']:.1f})."
                    ),
                    details={"event": "pair_graph", **pair_graph_stats},
                )
                match_cmd = [
                    "colmap",
                    "matches_importer",
                    "--database_path",
                    db_path,
                    "--match_list_path",
                    pair_list_path,
                    "--match_type",
                    "pairs",
                    "--FeatureMatching.type",
                    resolved_matcher_type,
                    "--FeatureMatching.use_gpu",
                    "1",
                    "--FeatureMatching.gpu_index",
                    feature_gpu_index,
                    "--FeatureMatching.guided_matching",
                    "1" if params.get("guided_matching", False) else "0",
                ]
            elif matching_strategy == "sequential":
                match_cmd = [
                    "colmap",
                    "sequential_matcher",
                    "--database_path",
                    db_path,
                    "--FeatureMatching.type",
                    resolved_matcher_type,
                    "--FeatureMatching.use_gpu",
                    "1",
                    "--FeatureMatching.gpu_index",
                    feature_gpu_index,
                    "--FeatureMatching.guided_matching",
                    "1" if params.get("guided_matching", False) else "0",
                ]
            else:
                if matching_strategy == "gps_pairs":
                    report_mission_progress(
                        vol_id,
                        "MATCHING",
                        25,
                        log="GPS pair selection unavailable; using bounded COLMAP spatial matching.",
                    )
                match_cmd = [
                    "colmap",
                    "spatial_matcher",
                    "--database_path",
                    db_path,
                    "--SpatialMatching.ignore_z",
                    "1",
                    "--SpatialMatching.max_num_neighbors",
                    str(params["gps_pair_max_neighbors"]),
                    "--SpatialMatching.min_num_neighbors",
                    str(params["gps_pair_min_neighbors"]),
                    "--FeatureMatching.type",
                    resolved_matcher_type,
                    "--FeatureMatching.use_gpu",
                    "1",
                    "--FeatureMatching.gpu_index",
                    feature_gpu_index,
                    "--FeatureMatching.guided_matching",
                    "1" if params.get("guided_matching", False) else "0",
                ]

            match_cmd += matching_model_options
            run_command(
                match_cmd,
                vol_id,
                "MATCHING",
                30,
                report_mission_progress,
                ensure_not_cancelled,
            )
            match_counts = database_counts(db_path)
            report_mission_progress(
                vol_id,
                "MATCHING",
                34,
                log=(
                    f"Verified {match_counts['two_view_geometries']} image pairs "
                    f"for {match_counts['images']} images."
                ),
                details={"event": "matching_complete", **match_counts},
            )

            gravity_available = False
            if bool(params.get("imu_gravity_enabled", False)):
                orientation_records = load_rtk_records(clean_images_dir)
                gravity_report = inject_database_gravity_priors(
                    db_path,
                    orientation_records,
                )
                gravity_available = bool(
                    gravity_report["use_in_global_rotation_averaging"]
                )
                atomic_write_json(
                    os.path.join(workspace_dir, "imu_gravity_report.json"),
                    gravity_report,
                )
                report_mission_progress(
                    vol_id,
                    "MATCHING",
                    35,
                    log=(
                        "Enabled gimbal-derived gravity for global rotation "
                        f"averaging ({gravity_report['attitude_pose_priors']}/"
                        f"{gravity_report['database_pose_priors']} images)."
                        if gravity_available
                        else "Gimbal gravity skipped: less than 95% of database "
                        "images have complete compatible attitude metadata."
                    ),
                    details={"event": "imu_gravity", **gravity_report},
                )
            
            # --- 5. SfM: View Graph Calibration (modern only) ---
            if params["use_view_graph_calibrator"]:
                report_mission_progress(vol_id, "CALIBRATING", 38, log="Running view graph calibration for GLOMAP...")
                run_command([
                    "colmap", "view_graph_calibrator",
                    "--database_path", db_path,
                ], vol_id, "CALIBRATING", 38, report_mission_progress, ensure_not_cancelled)
            
            # --- 6. SfM: Mapping ---
            os.makedirs(sparse_path, exist_ok=True)
            requested_engine = str(params.get("alignment_engine", "auto")).lower()
            if requested_engine not in {"auto", "glomap", "caspar", "ceres"}:
                raise ValueError(f"Unsupported alignment engine: {requested_engine}")
            mapping_timeout = float(params["mapping_timeout_seconds"])
            mapping_started_at = time.monotonic()
            minimum_registration_ratio = float(params["minimum_registration_ratio"])
            minimum_registered_images = max(
                3,
                int(math.ceil(len(images) * minimum_registration_ratio)),
            )
            maximum_reprojection_error = float(
                params["maximum_mean_reprojection_error_px"]
            )
            minimum_track_length = float(
                params["minimum_median_track_length"]
            )

            def passes_sparse_quality(quality):
                reprojection_error = quality[
                    "mean_reprojection_error_px"
                ]
                track_length = quality["median_track_length"]
                return (
                    quality["registered_images"]
                    >= minimum_registered_images
                    and quality["points3D"] > 0
                    and reprojection_error is not None
                    and reprojection_error <= maximum_reprojection_error
                    and track_length is not None
                    and track_length >= minimum_track_length
                )

            def remaining_mapping_budget():
                remaining = mapping_timeout - (time.monotonic() - mapping_started_at)
                if remaining <= 0:
                    raise TimeoutError(
                        f"The shared {mapping_timeout:.0f}s mapping budget is exhausted."
                    )
                return remaining

            def run_mapping_engine(engine, progress):
                engine_timeout = remaining_mapping_budget()
                command = build_mapping_command(
                    engine,
                    database_path=db_path,
                    image_path=clean_images_dir,
                    output_path=sparse_path,
                    gpu_index=ba_gpu_index,
                    global_max_tracks=int(float(params["global_mapper_max_tracks"])),
                    global_ba_iterations=int(float(params["global_mapper_ba_iterations"])),
                    global_ceres_iterations=int(
                        float(params["global_mapper_ceres_iterations"])
                    ),
                    global_skip_retriangulation=bool(
                        params.get("global_mapper_skip_retriangulation", True)
                    ),
                    global_random_seed=int(
                        float(params["global_mapper_random_seed"])
                    ),
                    global_ba_min_track_length=int(
                        float(params["global_mapper_ba_min_track_length"])
                    ),
                    global_tri_complete_max_reproj_error=float(
                        params["global_mapper_tri_complete_max_reproj_error"]
                    ),
                    global_tri_merge_max_reproj_error=float(
                        params["global_mapper_tri_merge_max_reproj_error"]
                    ),
                    global_tri_min_angle=float(
                        params["global_mapper_tri_min_angle"]
                    ),
                    global_use_gravity=gravity_available,
                )
                report_mission_progress(
                    vol_id,
                    "MAPPING",
                    progress,
                    log=(
                        f"Starting alignment engine={engine} with a "
                        f"{engine_timeout:.0f}s remaining shared time budget."
                    ),
                    details={
                        "event": "alignment_engine_started",
                        "engine": engine,
                        "timeout_seconds": engine_timeout,
                    },
                )
                run_command(
                    command,
                    vol_id,
                    "MAPPING",
                    progress,
                    report_mission_progress,
                    ensure_not_cancelled,
                    timeout_seconds=engine_timeout,
                )

            primary_engine = "glomap" if requested_engine == "auto" else requested_engine
            if primary_engine == "caspar":
                caspar_supported, camera_models = caspar_compatibility(db_path)
                if not caspar_supported:
                    raise RuntimeError(
                        "Caspar only supports PINHOLE and SIMPLE_RADIAL cameras; "
                        f"database contains {sorted(camera_models)}."
                    )
            primary_error = None
            try:
                run_mapping_engine(primary_engine, 45)
            except (RuntimeError, subprocess.CalledProcessError, TimeoutError) as error:
                primary_error = error
                if requested_engine != "auto":
                    raise
                report_mission_progress(
                    vol_id,
                    "MAPPING",
                    46,
                    log=(
                        f"Primary GLOMAP attempt failed within its bounded budget: "
                        f"{type(error).__name__}: {error}"
                    ),
                    details={
                        "event": "alignment_engine_failed",
                        "engine": primary_engine,
                        "error": str(error),
                    },
                )

            sparse_model_path = os.path.join(sparse_path, "0")
            quality = inspect_sparse_quality(sparse_model_path)
            registered_images = quality["registered_images"]
            sparse_points = quality["points3D"]
            primary_usable = (
                primary_error is None
                and passes_sparse_quality(quality)
            )
            report_mission_progress(
                vol_id,
                "MAPPING",
                46,
                log=(
                    f"{primary_engine} registered {registered_images}/{len(images)} "
                    f"images with {sparse_points} points; "
                    f"required={minimum_registered_images}."
                ),
                details={
                    "event": "alignment_quality_gate",
                    "engine": primary_engine,
                    "registered_images": registered_images,
                    "total_images": len(images),
                    "points3D": sparse_points,
                    "minimum_registered_images": minimum_registered_images,
                    "maximum_mean_reprojection_error_px": (
                        maximum_reprojection_error
                    ),
                    "minimum_median_track_length": minimum_track_length,
                    **quality,
                    "accepted": primary_usable,
                },
            )

            if not primary_usable and requested_engine == "auto":
                caspar_supported, camera_models = caspar_compatibility(db_path)
                fallback_engine = choose_auto_fallback(camera_models)
                report_mission_progress(
                    vol_id,
                    "MAPPING",
                    47,
                    log=(
                        f"GLOMAP quality gate failed. Reusing the existing features "
                        f"and {match_counts['two_view_geometries']} verified pairs with "
                        f"incremental {fallback_engine.upper()} BA. "
                        f"Camera models: {sorted(camera_models)}."
                    ),
                    details={
                        "event": "alignment_fallback",
                        "from_engine": primary_engine,
                        "to_engine": fallback_engine,
                        "caspar_supported": caspar_supported,
                        "camera_models": sorted(camera_models),
                    },
                )
                shutil.rmtree(sparse_path, ignore_errors=True)
                os.makedirs(sparse_path, exist_ok=True)
                run_mapping_engine(fallback_engine, 48)
                quality = inspect_sparse_quality(sparse_model_path)
                registered_images = quality["registered_images"]
                sparse_points = quality["points3D"]
                primary_engine = fallback_engine

            if not passes_sparse_quality(quality):
                raise RuntimeError(
                    "Sparse reconstruction failed the alignment quality gate "
                    f"after {primary_engine}: registered_images={registered_images}/"
                    f"{len(images)}, required={minimum_registered_images}, "
                    f"points3D={sparse_points}, "
                    f"mean_reprojection_error_px="
                    f"{quality['mean_reprojection_error_px']}, "
                    f"median_track_length={quality['median_track_length']}. "
                    "Exhaustive matching and unbounded "
                    "CPU bundle adjustment are intentionally disabled."
                )
        else:
            report_mission_progress(vol_id, "MAPPING", 45, log="Sparse model found. Skipping SfM extraction and matching.")

        # --- 6b. Optional covariance-aware RTK/PPK refinement ---
        base_sparse_model_path = os.path.join(sparse_path, "0")
        rtk_sparse_model_path = os.path.join(workspace_dir, "sparse_rtk")
        rtk_report_path = os.path.join(workspace_dir, "rtk_prior_report.json")
        rtk_refinement_enabled = bool(params.get("rtk_refinement_enabled", True))
        active_sparse_model_path = (
            rtk_sparse_model_path
            if rtk_refinement_enabled
            and os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin"))
            else base_sparse_model_path
        )
        if (
            rtk_refinement_enabled
            and not ortho_only_ready
            and os.path.exists(os.path.join(base_sparse_model_path, "cameras.bin"))
        ):
            if os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin")):
                active_sparse_model_path = rtk_sparse_model_path
                report_mission_progress(
                    vol_id,
                    "RTK_REFINEMENT",
                    57,
                    log="Reusing the completed covariance-aware RTK sparse model.",
                )
            else:
                try:
                    rtk_records = load_rtk_records(clean_images_dir)
                    rtk_report = inject_database_pose_priors(db_path, rtk_records)
                    rtk_report["status"] = "priors-injected"
                    atomic_write_json(rtk_report_path, rtk_report)
                    os.makedirs(rtk_sparse_model_path, exist_ok=True)
                    rtk_timeout = float(params["rtk_refinement_timeout_seconds"])
                    rtk_iterations = int(float(params["rtk_refinement_iterations"]))
                    rtk_loss_scale = float(params["rtk_refinement_loss_scale"])
                    report_mission_progress(
                        vol_id,
                        "RTK_REFINEMENT",
                        55,
                        log=(
                            f"Constraining the completed visual reconstruction with "
                            f"{rtk_report['updated_pose_priors']} camera-position priors "
                            f"and MRK/XMP RTK covariance using robust Ceres GPU BA, "
                            f"{rtk_iterations} iterations and a {rtk_timeout:.0f}s budget."
                        ),
                        details={"event": "rtk_refinement_started", **rtk_report},
                    )
                    rtk_started_at = time.monotonic()
                    run_command(
                        [
                            "colmap",
                            "pose_prior_mapper",
                            "--database_path",
                            db_path,
                            "--image_path",
                            clean_images_dir,
                            "--input_path",
                            base_sparse_model_path,
                            "--output_path",
                            rtk_sparse_model_path,
                            "--Mapper.ba_use_gpu",
                            "1",
                            "--Mapper.ba_gpu_index",
                            ba_gpu_index,
                            "--Mapper.ba_local_backend",
                            "CERES",
                            "--Mapper.ba_global_backend",
                            "CERES",
                            "--Mapper.ba_local_max_num_iterations",
                            str(rtk_iterations),
                            "--Mapper.ba_global_max_num_iterations",
                            str(rtk_iterations),
                            "--Mapper.ba_local_max_refinements",
                            "1",
                            "--Mapper.ba_global_max_refinements",
                            "1",
                            "--Mapper.ba_global_ignore_redundant_points3D",
                            "1",
                            "--use_robust_loss_on_prior_position",
                            "1",
                            "--prior_position_loss_scale",
                            str(rtk_loss_scale),
                        ],
                        vol_id,
                        "RTK_REFINEMENT",
                        56,
                        report_mission_progress,
                        ensure_not_cancelled,
                        timeout_seconds=rtk_timeout,
                    )
                    if not os.path.exists(
                        os.path.join(rtk_sparse_model_path, "cameras.bin")
                    ):
                        raise RuntimeError(
                            "pose_prior_mapper did not write a usable RTK model"
                        )
                    active_sparse_model_path = rtk_sparse_model_path
                    rtk_report.update(
                        {
                            "status": "completed",
                            "elapsed_seconds": time.monotonic() - rtk_started_at,
                            "iterations": rtk_iterations,
                            "timeout_seconds": rtk_timeout,
                            "ba_backend": "CERES_GPU",
                            "robust_loss": "cauchy",
                            "robust_loss_scale": rtk_loss_scale,
                        }
                    )
                    atomic_write_json(rtk_report_path, rtk_report)
                    for stale_path in (
                        dense_path,
                        os.path.join(workspace_dir, "sparse_geo"),
                    ):
                        shutil.rmtree(stale_path, ignore_errors=True)
                    for stale_path in (
                        os.path.join(workspace_dir, "alignment_transform.json"),
                        os.path.join(workspace_dir, "orthomosaic.tif"),
                        os.path.join(workspace_dir, "orthomosaic.height.tif"),
                        os.path.join(workspace_dir, "gcp_alignment_report.json"),
                    ):
                        if os.path.exists(stale_path):
                            os.remove(stale_path)
                    report_mission_progress(
                        vol_id,
                        "RTK_REFINEMENT",
                        58,
                        log=(
                            "RTK pose refinement completed; downstream undistortion "
                            "and georeferencing will use the constrained model."
                        ),
                        details={"event": "rtk_refinement_completed", **rtk_report},
                    )
                except (
                    RuntimeError,
                    subprocess.CalledProcessError,
                    TimeoutError,
                ) as error:
                    shutil.rmtree(rtk_sparse_model_path, ignore_errors=True)
                    fallback_report = {
                        "schema_version": 1,
                        "status": "skipped-or-fallback",
                        "reason": str(error),
                        "fallback_model": base_sparse_model_path,
                    }
                    atomic_write_json(rtk_report_path, fallback_report)
                    report_mission_progress(
                        vol_id,
                        "RTK_REFINEMENT",
                        58,
                        log=(
                            f"RTK refinement unavailable or bounded attempt failed "
                            f"({error}); retaining the verified fast sparse model."
                        ),
                        details={"event": "rtk_refinement_fallback", **fallback_report},
                    )

        # --- 7. Undistort images for Gaussian Splatting ---
        # GS only needs the undistorted images + dense/sparse model.
        if ortho_only_ready:
            report_mission_progress(vol_id, "UNDISTORT", 75, log="Undistorted images found. Skipping undistortion.")
        else:
            if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
                run_command([
                    "colmap", "image_undistorter",
                    "--image_path", clean_images_dir,
                    "--input_path", active_sparse_model_path,
                    "--output_path", dense_path,
                    "--max_image_size", params["mvs_max_image_size"],
                ], vol_id, "UNDISTORT", 70, report_mission_progress, ensure_not_cancelled)
            else:
                report_mission_progress(vol_id, "UNDISTORT", 70, log="Undistorted images and fusion.cfg found. Skipping undistortion.")

            dense_sparse_ready = dense_sparse_model_ready(dense_path)
            n_undistorted = len(os.listdir(os.path.join(dense_path, "images"))) if os.path.isdir(os.path.join(dense_path, "images")) else 0
            report_mission_progress(
                vol_id, "UNDISTORT", 90,
                log=f"Using {n_undistorted} undistorted images for Gaussian Splatting.",
            )
        
        # --- 8. Geo-alignment ---
        sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")

        def ensure_alignment_transform():
            nonlocal align_tf
            gcp_adjustment_enabled = bool(
                params.get("gcp_adjustment_enabled", False)
            )
            transform_file = os.path.join(
                workspace_dir,
                "alignment_transform.json",
            )
            if gcp_adjustment_enabled:
                if not gcp_path:
                    raise RuntimeError(
                        "Weighted GCP adjustment is enabled but the dataset does "
                        "not contain gcp_list.txt"
                    )
                report_mission_progress(
                    vol_id,
                    "ALIGNING",
                    93,
                    log=(
                        "Triangulating surveyed controls and fitting a robust "
                        "covariance-weighted GCP similarity transform..."
                    ),
                )
                transform, gcp_report = build_weighted_gcp_alignment(
                    active_sparse_model_path,
                    gcp_path,
                    utm_crs,
                    accuracy_path=gcp_accuracy_path,
                    default_horizontal_accuracy_m=float(
                        params["gcp_horizontal_accuracy_m"]
                    ),
                    default_vertical_accuracy_m=float(
                        params["gcp_vertical_accuracy_m"]
                    ),
                    default_image_accuracy_px=float(
                        params["gcp_image_accuracy_px"]
                    ),
                    robust_loss_scale=float(params["gcp_robust_loss_scale"]),
                )
                from shared.geo_alignment import write_alignment_transform

                write_alignment_transform(transform_file, transform)
                write_transformed_reconstruction(
                    active_sparse_model_path,
                    sparse_geo_path,
                    transform,
                )
                atomic_write_json(
                    os.path.join(workspace_dir, "gcp_alignment_report.json"),
                    gcp_report,
                )
                align_tf = transform_file
                fit = transform["fit"]
                report_mission_progress(
                    vol_id,
                    "ALIGNING",
                    94,
                    log=(
                        "Saved weighted GCP alignment using "
                        f"{gcp_report['adjustment_points']} adjustment controls "
                        f"and {gcp_report['checkpoint_points']} independent "
                        f"checkpoints (RMSE={fit['rmse']:.3f} m, "
                        f"weighted RMSE={fit['weighted_rmse']:.2f}σ)."
                    ),
                    details={
                        "event": "gcp_alignment_completed",
                        "adjustment_points": gcp_report["adjustment_points"],
                        "checkpoint_points": gcp_report["checkpoint_points"],
                        **fit,
                    },
                )
                return align_tf

            stale_gcp_report = os.path.join(
                workspace_dir,
                "gcp_alignment_report.json",
            )
            stale_gcp_alignment = os.path.exists(stale_gcp_report)
            if os.path.exists(stale_gcp_report):
                os.remove(stale_gcp_report)

            if align_tf and os.path.exists(align_tf):
                try:
                    with open(align_tf, encoding="utf-8") as handle:
                        previous_alignment = json.load(handle)
                    if (
                        previous_alignment.get("fit", {}).get("source")
                        == "covariance_weighted_gcp"
                    ):
                        os.remove(align_tf)
                        align_tf = None
                        stale_gcp_alignment = True
                except json.JSONDecodeError:
                    os.remove(align_tf)
                    align_tf = None
                    stale_gcp_alignment = True
                except FileNotFoundError:
                    align_tf = None
                except OSError as error:
                    raise RuntimeError(
                        f"Cannot inspect cached alignment transform: {error}"
                    ) from error

            if stale_gcp_alignment:
                shutil.rmtree(sparse_geo_path, ignore_errors=True)

            if not (
                os.path.exists(geo_data_file)
                and os.path.getsize(geo_data_file) > 0
            ):
                return None

            os.makedirs(sparse_geo_path, exist_ok=True)
            align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin"))
            if not align_done:
                run_command([
                    "colmap", "model_aligner",
                    "--input_path", active_sparse_model_path,
                    "--output_path", sparse_geo_path,
                    "--ref_images_path", geo_data_file,
                    "--ref_is_gps", "0",
                    "--alignment_max_error", str(params["alignment_max_error"]),
                ], vol_id, "ALIGNING", 93, report_mission_progress, ensure_not_cancelled)

            if align_tf and os.path.exists(align_tf):
                return align_tf

            report_mission_progress(vol_id, "ALIGNING", 94, log="Computing sparse-model alignment transform for orthorectification...")
            try:
                from shared.geo_alignment import (
                    compute_reconstruction_alignment,
                    write_alignment_transform,
                )

                transform = compute_reconstruction_alignment(
                    active_sparse_model_path,
                    sparse_geo_path,
                )
                write_alignment_transform(transform_file, transform)
                align_tf = transform_file
                fit = transform["fit"]
                report_mission_progress(
                    vol_id,
                    "ALIGNING",
                    94,
                    log=(
                        "Saved sparse-model alignment transform using "
                        f"{fit['correspondences']} images "
                        f"(scale={transform['scale']:.4f}, RMSE={fit['rmse']:.3f} m)"
                    ),
                )
                return align_tf
            except Exception as e:
                report_mission_progress(vol_id, "ALIGNING", 94, log=f"Failed to compute alignment transform ({e}); using raw COLMAP coordinates.")
                return None
        
        ensure_alignment_transform()
        
        # --- 9. Gaussian Splatting Orthomosaic ---
        ortho_file = os.path.join(workspace_dir, "orthomosaic.tif")
        
        align_tf_path = os.path.join(workspace_dir, "alignment_transform.json")
        if os.path.exists(align_tf_path):
            align_tf = align_tf_path

        dense_sparse_ready = dense_sparse_model_ready(dense_path)
        if not dense_sparse_ready:
            raise RuntimeError(
                "Gaussian Splatting requires dense/sparse model (cameras.bin, images.bin, points3D.bin). "
                f"dense_sparse_ready={dense_sparse_ready}."
            )
        try:
            import gc
            import traceback as _tb
            app1_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
            if app1_dir not in sys.path:
                sys.path.insert(0, app1_dir)
            from gaussian_ortho.generate_gaussian_orthophoto import generate_gaussian_orthophoto

            gc.collect()
            try:
                import cupy as _cp
                _cp.get_default_memory_pool().free_all_blocks()
                _cp.get_default_pinned_memory_pool().free_all_blocks()
            except Exception:
                pass

            ortho_resolution = float(params.get("ortho_mesh_resolution", 0.02))

            # Auto data_factor: preserve all source detail that max_width can
            # consume. Dataset count is a memory/runtime concern handled by
            # tile mode and Gaussian caps, not a reason to blur every image.
            gs_data_factor_raw = str(params.get("gs_data_factor", "auto"))
            if gs_data_factor_raw == "auto":
                images_dir = os.path.join(dense_path, "images")
                image_files = [f for f in os.listdir(images_dir)
                               if f.lower().endswith(('.jpg', '.jpeg', '.png'))] if os.path.isdir(images_dir) else []
                max_dim = 0
                if image_files:
                    try:
                        probe_path = os.path.join(images_dir, image_files[0])
                        with PILImage.open(probe_path) as img:
                            max_dim = max(img.size)
                    except Exception:
                        pass
                max_training_width = int(
                    params.get(
                        "gs_max_width",
                        DRONEGS_PRODUCTION_PROFILE_V1.max_width,
                    )
                )
                gs_data_factor = choose_dronegs_data_factor(
                    max_dim, max_training_width
                )
                report_mission_progress(
                    vol_id,
                    "GAUSS",
                    95,
                    log=(
                        f"Auto data_factor={gs_data_factor} preserves the "
                        f"configured {max_training_width}px training ceiling "
                        f"from a {max_dim}px source."
                    ),
                )
            else:
                gs_data_factor = int(gs_data_factor_raw)

            gs_iterations = int(
                params.get(
                    "gs_iterations",
                    DRONEGS_PRODUCTION_PROFILE_V1.iterations,
                )
            )
            gs_cap_max = int(
                params.get(
                    "gs_cap_max",
                    DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
                )
            )
            gs_sh_degree = int(
                params.get(
                    "gs_sh_degree",
                    DRONEGS_PRODUCTION_PROFILE_V1.sh_degree,
                )
            )
            gs_backend = str(params.get("gs_backend", "dronegs"))
            gs_seed = int(params.get("gs_seed", 42))
            gs_profile_id = str(
                params.get(
                    "gs_production_profile",
                    DRONEGS_PRODUCTION_PROFILE_V1.profile_id,
                )
            )
            gs_optimizer_profile = str(
                params.get(
                    "gs_optimizer_profile",
                    DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile,
                )
            )
            gs_pruning_policy = str(
                params.get(
                    "gs_pruning_policy",
                    DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy,
                )
            )
            gs_raster_profile = str(
                params.get(
                    "gs_raster_profile",
                    DRONEGS_PRODUCTION_PROFILE_V1.raster_profile,
                )
            )
            gs_sh_degree_interval = int(
                params.get("gs_sh_degree_interval", 1_000)
            )
            gs_topology_cooldown = int(
                params.get("gs_topology_cooldown", 1_000)
            )
            gs_photometric_finish = int(
                params.get("gs_photometric_finish", 1_000)
            )
            gs_photometric_mse_percent = int(
                params.get("gs_photometric_mse_percent", 100)
            )
            gs_checkpoint_every = int(
                params.get("gs_checkpoint_every", 2_000)
            )
            gs_test_every = int(params.get("gs_test_every", 8))
            gs_test_split = str(params.get("gs_test_split", "modulo"))
            gs_test_guard_percent = int(
                params.get("gs_test_guard_percent", 0)
            )
            gs_canary_min_psnr = float(
                params.get("gs_canary_min_psnr", 18.0)
            )
            gs_canary_min_ssim = float(
                params.get("gs_canary_min_ssim", 0.35)
            )
            gs_filter_enabled = params.get("gs_filter_enabled", True)
            gs_filter_max_scale = float(params.get("gs_filter_max_scale", 1.0))
            gs_filter_dist = float(params.get("gs_filter_dist", 1.0))
            gs_filter_opacity = float(params.get("gs_filter_opacity", 0.005))
            gs_filter_needle = float(params.get("gs_filter_needle", 0.0))
            gs_filter_sor = params.get("gs_filter_sor", False)
            gs_filter_sor_sigma = float(params.get("gs_filter_sor_sigma", 4.0))
            gs_filter_cc = params.get("gs_filter_cc", False)
            gs_filter_z_floater = params.get("gs_filter_z_floater", False)
            if gs_profile_id == DRONEGS_PRODUCTION_PROFILE_V1.profile_id:
                production = DRONEGS_PRODUCTION_PROFILE_V1
                profile_values = {
                    "iterations": gs_iterations,
                    "data_factor": gs_data_factor,
                    "max_width": int(
                        params.get(
                            "gs_max_width",
                            DRONEGS_PRODUCTION_PROFILE_V1.max_width,
                        )
                    ),
                    "tile_mode": int(
                        params.get(
                            "gs_tile_mode",
                            DRONEGS_PRODUCTION_PROFILE_V1.tile_mode,
                        )
                    ),
                    "cap_max": gs_cap_max,
                    "sh_degree": gs_sh_degree,
                    "seed": gs_seed,
                    "optimizer_profile": gs_optimizer_profile,
                    "pruning_policy": gs_pruning_policy,
                    "raster_profile": gs_raster_profile,
                    "sh_degree_interval": gs_sh_degree_interval,
                    "topology_cooldown": gs_topology_cooldown,
                    "photometric_finish": gs_photometric_finish,
                    "photometric_mse_percent": (
                        gs_photometric_mse_percent
                    ),
                    "checkpoint_every": gs_checkpoint_every,
                    "test_every": gs_test_every,
                    "test_split": gs_test_split,
                    "test_guard_percent": gs_test_guard_percent,
                    "canary_min_psnr": gs_canary_min_psnr,
                    "canary_min_ssim": gs_canary_min_ssim,
                }
                expected_profile_values = {
                    name: getattr(production, name)
                    for name in profile_values
                }
                if profile_values != expected_profile_values:
                    gs_profile_id = "custom"
                    report_mission_progress(
                        vol_id,
                        "GAUSS",
                        94,
                        log=(
                            "DroneGS expert overrides detected; the run is "
                            "recorded as custom instead of production V1."
                        ),
                    )

            checkpoint_root = os.getenv("DRONEGS_CHECKPOINT_ROOT")
            if not checkpoint_root:
                checkpoint_root = os.path.join(
                    os.path.dirname(workspace_dir),
                    ".dronegs-checkpoints",
                )
            durable_checkpoint_dir = os.path.join(checkpoint_root, vol_id)
            os.makedirs(durable_checkpoint_dir, exist_ok=True)
            checkpoint_s3_prefix = (
                f"{mission_s3_prefix}/gaussian-checkpoints"
            )
            if not any(
                path.is_file()
                for path in Path(durable_checkpoint_dir).rglob("*")
            ):
                try:
                    restored_count = storage.download_directory(
                        checkpoint_s3_prefix + "/",
                        durable_checkpoint_dir,
                    )
                    if restored_count:
                        report_mission_progress(
                            vol_id,
                            "GAUSS",
                            94,
                            log=(
                                "Restored "
                                f"{restored_count} durable DroneGS artifacts "
                                "from S3."
                            ),
                        )
                except Exception as restore_error:
                    report_mission_progress(
                        vol_id,
                        "GAUSS",
                        94,
                        log=(
                            "No remote DroneGS recovery state restored: "
                            f"{restore_error}"
                        ),
                    )

            def persist_dronegs_checkpoint(checkpoint_path, iteration):
                relative = checkpoint_path.resolve().relative_to(
                    Path(durable_checkpoint_dir).resolve()
                )
                s3_key = (
                    f"{checkpoint_s3_prefix}/{relative.as_posix()}"
                )
                try:
                    storage.upload_file(checkpoint_path, s3_key)
                    report_mission_progress(
                        vol_id,
                        "GAUSS",
                        95,
                        log=(
                            f"Durable DroneGS checkpoint synced at "
                            f"iteration {iteration}."
                        ),
                    )
                except Exception as sync_error:
                    report_mission_progress(
                        vol_id,
                        "GAUSS",
                        95,
                        log=(
                            "DroneGS checkpoint remains locally durable; "
                            f"S3 sync failed: {sync_error}"
                        ),
                    )

            result = generate_gaussian_orthophoto(
                dense_path=dense_path,
                ortho_file=ortho_file,
                utm_crs=utm_crs,
                vol_id=vol_id,
                transform_file=align_tf,
                report_fn=report_mission_progress,
                resolution=ortho_resolution,
                iterations=gs_iterations,
                sh_degree=gs_sh_degree,
                data_factor=gs_data_factor,
                max_width=int(
                    params.get(
                        "gs_max_width",
                        DRONEGS_PRODUCTION_PROFILE_V1.max_width,
                    )
                ),
                ortho_mip_filter_variance=float(
                    params.get("gs_ortho_mip_filter_variance", 0.03)
                ),
                ortho_mip_filter_compensation=bool(
                    params.get("gs_ortho_mip_filter_compensation", True)
                ),
                tile_mode=int(
                    params.get(
                        "gs_tile_mode",
                        DRONEGS_PRODUCTION_PROFILE_V1.tile_mode,
                    )
                ),
                cap_max=gs_cap_max,
                filter_enabled=gs_filter_enabled,
                filter_max_scale=gs_filter_max_scale,
                filter_dist_multiplier=gs_filter_dist,
                filter_opacity_threshold=gs_filter_opacity,
                filter_needle_ratio=gs_filter_needle,
                filter_sor=gs_filter_sor,
                filter_sor_sigma=gs_filter_sor_sigma,
                filter_cc=gs_filter_cc,
                filter_z_floater=gs_filter_z_floater,
                checkpoint_dir=durable_checkpoint_dir,
                trainer_backend=gs_backend,
                training_seed=gs_seed,
                dronegs_profile_id=gs_profile_id,
                dronegs_optimizer_profile=gs_optimizer_profile,
                dronegs_pruning_policy=gs_pruning_policy,
                dronegs_raster_profile=gs_raster_profile,
                dronegs_sh_degree_interval=gs_sh_degree_interval,
                dronegs_topology_cooldown=gs_topology_cooldown,
                dronegs_photometric_finish=gs_photometric_finish,
                dronegs_photometric_mse_percent=gs_photometric_mse_percent,
                dronegs_checkpoint_every=gs_checkpoint_every,
                dronegs_test_every=gs_test_every,
                dronegs_test_split=gs_test_split,
                dronegs_test_guard_percent=gs_test_guard_percent,
                dronegs_canary_min_psnr=gs_canary_min_psnr,
                dronegs_canary_min_ssim=gs_canary_min_ssim,
                cancellation_check=ensure_not_cancelled,
                checkpoint_callback=persist_dronegs_checkpoint,
            )
            report_mission_progress(vol_id, "GAUSS", 100,
                log=f"Gaussian Splatting orthomosaic complete: {result['width']}x{result['height']}px, "
                    f"{result['n_gaussians']} Gaussians, GSD={ortho_resolution}m")
        except Exception as e:
            _tb.print_exc()
            report_mission_progress(vol_id, "ORTHO", 95, log=f"Gaussian Splatting ortho failed: {e}")
            raise

        # --- Upload ALL artifacts to S3 (well-organized folders) ---
        # S3 layout:
        #   missions/{vol_id}/
        #     orthomosaic.tif          — final GeoTIFF
        #     orthomosaic.height.tif   — height map (if generated)
        #     alignment_transform.json — Sim3 geo-alignment
        #     gcp_alignment_report.json — weighted controls/checkpoints (optional)
        #     imu_gravity_report.json   — attitude coverage/provenance (optional)
        #     geo_data.txt             — GPS from EXIF
        #     geo_data.txt.crs         — projected metric CRS code
        #     geo_data.txt.crs.json    — CRS selection policy and provenance
        #     colmap/
        #       database.db            — COLMAP feature database
        #       sparse/0/              — SfM sparse model (cameras.bin, images.bin, points3D.bin)
        #       sparse_geo/            — Geo-registered sparse model
        #     dense/
        #       sparse/0/              — Undistorted model
        #       images/                — Undistorted images
        #     gaussian/
        #       final.ply              — Merged Gaussian splat model
        #       full/splat_*.ply       — Training output PLY
        #       full/checkpoints/      — Resume checkpoint

        report_mission_progress(
            vol_id,
            "UPLOADING",
            90,
            log="Converting orthomosaic to a tiled COG and publishing verified assets...",
        )
        upload_count = 0
        ortho_s3_key = f"{mission_s3_prefix}/orthomosaic.tif"
        if not os.path.isfile(ortho_file):
            raise FileNotFoundError(
                f"Required orthomosaic artifact is missing: {ortho_file}"
            )

        # The final raster is a required, independently verifiable product.
        # Never announce DONE or start downstream processing unless conversion,
        # upload and remote integrity verification all succeeded.
        convert_to_cog(ortho_file)
        storage.upload_verified_file(ortho_file, ortho_s3_key)
        storage.upload_verified_file(
            metadata_path(ortho_file),
            f"{ortho_s3_key}.cog.json",
        )
        storage.upload_verified_file(
            preview_path(ortho_file),
            f"{mission_s3_prefix}/orthomosaic.preview.webp",
        )
        upload_count += 3
        report_mission_progress(
            vol_id,
            "UPLOADING",
            92,
            log="Verified COG, map metadata and bounded preview uploaded",
        )

        # Remaining recovery/debug products are useful but do not invalidate a
        # successfully published orthomosaic when one of them cannot be copied.
        try:
            height_tif = os.path.join(workspace_dir, "orthomosaic.height.tif")
            if os.path.exists(height_tif):
                try:
                    convert_to_cog(height_tif)
                    height_key = (
                        f"{mission_s3_prefix}/orthomosaic.height.tif"
                    )
                    for local_path, remote_key in (
                        (height_tif, height_key),
                        (
                            metadata_path(height_tif),
                            f"{height_key}.cog.json",
                        ),
                        (
                            preview_path(height_tif),
                            f"{mission_s3_prefix}/"
                            "orthomosaic.height.preview.webp",
                        ),
                    ):
                        storage.upload_verified_file(local_path, remote_key)
                        upload_count += 1
                except Exception as height_error:
                    report_mission_progress(
                        vol_id,
                        "UPLOADING",
                        93,
                        log=(
                            "Warning: optional height COG publication "
                            f"failed: {height_error}"
                        ),
                    )

            # 2. Alignment & geo data
            if align_tf and os.path.exists(align_tf):
                storage.upload_file(align_tf, f"{mission_s3_prefix}/alignment_transform.json")
                upload_count += 1
            gcp_report_file = os.path.join(
                workspace_dir, "gcp_alignment_report.json"
            )
            if os.path.exists(gcp_report_file):
                storage.upload_file(
                    gcp_report_file,
                    f"{mission_s3_prefix}/gcp_alignment_report.json",
                )
                upload_count += 1
            imu_report_file = os.path.join(
                workspace_dir, "imu_gravity_report.json"
            )
            if os.path.exists(imu_report_file):
                storage.upload_file(
                    imu_report_file,
                    f"{mission_s3_prefix}/imu_gravity_report.json",
                )
                upload_count += 1
            if os.path.exists(geo_data_file):
                storage.upload_file(geo_data_file, f"{mission_s3_prefix}/geo_data.txt")
                upload_count += 1
                crs_file = f"{geo_data_file}.crs"
                if os.path.exists(crs_file):
                    storage.upload_file(crs_file, f"{mission_s3_prefix}/geo_data.txt.crs")
                    upload_count += 1
                crs_metadata_file = f"{geo_data_file}.crs.json"
                if os.path.exists(crs_metadata_file):
                    storage.upload_file(
                        crs_metadata_file,
                        f"{mission_s3_prefix}/geo_data.txt.crs.json",
                    )
                    upload_count += 1

            report_mission_progress(vol_id, "UPLOADING", 92, log="Geo data uploaded")

            # 3. COLMAP database + sparse models
            if os.path.exists(db_path):
                storage.upload_file(db_path, f"{mission_s3_prefix}/colmap/database.db")
                upload_count += 1

            sparse_0_path = os.path.join(sparse_path, "0")
            if os.path.isdir(sparse_0_path):
                n = storage.upload_directory(sparse_0_path, f"{mission_s3_prefix}/colmap/sparse/0/")
                upload_count += n

            sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
            if os.path.isdir(sparse_geo_path):
                n = storage.upload_directory(sparse_geo_path, f"{mission_s3_prefix}/colmap/sparse_geo/")
                upload_count += n

            report_mission_progress(vol_id, "UPLOADING", 94, log="COLMAP sparse models uploaded")

            # 4. Dense reconstruction (undistorted model + images)
            if os.path.isdir(dense_path):
                n = storage.upload_directory(dense_path, f"{mission_s3_prefix}/dense/")
                upload_count += n
                report_mission_progress(vol_id, "UPLOADING", 96, log=f"Dense reconstruction uploaded ({n} files)")

            # 5. Gaussian splatting outputs (PLY models + checkpoints)
            if (
                durable_checkpoint_dir
                and os.path.isdir(durable_checkpoint_dir)
            ):
                n = storage.upload_directory(
                    durable_checkpoint_dir,
                    f"{mission_s3_prefix}/gaussian/",
                )
                upload_count += n
                gaussian_upload_complete = True
                report_mission_progress(vol_id, "UPLOADING", 98, log=f"Gaussian models & checkpoints uploaded ({n} files)")

            report_mission_progress(vol_id, "UPLOADING", 99, log=f"All artifacts uploaded to S3 ({upload_count} files total)")
        except Exception as upload_err:
            report_mission_progress(
                vol_id,
                "UPLOADING",
                98,
                log=(
                    "Warning: an optional recovery/debug artifact could not "
                    f"be uploaded: {upload_err}"
                ),
            )

        # --- Cleanup local workspace to free WSL disk ---
        try:
            shutil.rmtree(workspace_dir, ignore_errors=True)
            report_mission_progress(vol_id, "CLEANUP", 99, log=f"Local workspace {workspace_dir} cleaned up")
        except Exception as cleanup_err:
            report_mission_progress(vol_id, "CLEANUP", 99, log=f"Warning: workspace cleanup failed: {cleanup_err}")

        report_mission_progress(vol_id, "DONE", 100, status="success", log="Pipeline complete!")
        publish_next_stage_message(producer, TOPIC_OUT, vol_id, ortho_s3_key, mission_params, normalize_ai_backend)
        if (
            gaussian_upload_complete
            and durable_checkpoint_dir
            and os.path.isdir(durable_checkpoint_dir)
        ):
            try:
                storage.delete_prefix(checkpoint_s3_prefix + "/")
                shutil.rmtree(durable_checkpoint_dir, ignore_errors=True)
                report_mission_progress(
                    vol_id,
                    "CLEANUP",
                    100,
                    log=(
                        "Durable DroneGS recovery state retired after "
                        "PLY/manifest/canary promotion."
                    ),
                )
            except Exception as retirement_error:
                report_mission_progress(
                    vol_id,
                    "CLEANUP",
                    100,
                    log=(
                        "Completed artifacts are promoted; recovery state "
                        f"was retained: {retirement_error}"
                    ),
                )

    except PipelineCancelledError as e:
        report_mission_progress(vol_id, "CANCELLED", 0, status="error", log=f"🚫 {str(e)}")
    except Exception as e:
        report_mission_progress(vol_id, "ERROR", 0, status="error", log=f"CRITICAL ERROR: {str(e)}")
        raise
    finally:
        # Always clean up local workspace to avoid filling the system disk.
        if os.path.isdir(workspace_dir):
            try:
                shutil.rmtree(workspace_dir, ignore_errors=True)
                report_mission_progress(vol_id, "CLEANUP", 99, log=f"Local workspace {workspace_dir} cleaned up (finally)")
            except Exception:
                pass
        # Free RAM/VRAM after every mission (success, cancel, or error).
        import gc
        gc.collect()
        try:
            import cupy as _cp
            _cp.get_default_memory_pool().free_all_blocks()
            _cp.get_default_pinned_memory_pool().free_all_blocks()
        except Exception:
            pass


def worker_main():
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    consumer = create_consumer(KAFKA_BROKER, TOPIC_IN)

    print("🎧 App 1 (COLMAP 4 — ALIKED/GLOMAP) ready.")

    def process_mission(mission):
        mission_context = None
        try:
            mission_context = build_mission_context(mission)
            cancellation_state.start_mission(mission_context.vol_id)
            previous_state = mission_state_tracker.start_mission(mission_context)

            log_mission_start(mission_context)
            if previous_state:
                resume_progress = previous_state.get("progress")
                if not isinstance(resume_progress, (int, float)):
                    resume_progress = 0
                resume_message = (
                    "Resuming from saved workspace state: "
                    f"status={previous_state.get('status', 'unknown')}, "
                    f"step={previous_state.get('step', 'unknown')}, "
                    f"progress={int(resume_progress)}%"
                )
                previous_log = previous_state.get("last_log")
                if previous_log:
                    resume_message += f", last_log={previous_log}"
                print(f"↻ {resume_message}")
                report_mission_progress(
                    mission_context.vol_id,
                    "RESUMING",
                    int(resume_progress),
                    log=resume_message,
                    details={
                        "event": "resume_detected",
                        "previous_state": {
                            "status": previous_state.get("status"),
                            "step": previous_state.get("step"),
                            "progress": previous_state.get("progress"),
                            "updated_at": previous_state.get("updated_at"),
                            "last_log": previous_state.get("last_log"),
                        },
                    },
                )

            if not mission_context.input_dir:
                raise ValueError(
                    f"No input dataset specified for mission {mission_context.vol_id}"
                )

            run_colmap_pipeline(
                mission_context.work_dir,
                mission_context.input_dir,
                mission_context.vol_id,
                mission_context.mission,
            )
        finally:
            if mission_context is not None:
                mission_state_tracker.clear_mission(mission_context.vol_id)
            cancellation_state.clear()

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue
            process_message(
                consumer=consumer,
                producer=producer,
                message=msg,
                consumer_group="colmap-workers-v4",
                expected_type="mission",
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=process_mission,
                logger=logger,
            )
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()


if __name__ == "__main__":
    worker_main()
