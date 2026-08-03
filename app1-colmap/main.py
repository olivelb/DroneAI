import os
import json
import math
import shutil
import subprocess
import sys
import threading
import time
import logging
from dataclasses import dataclass
from PIL import Image as PILImage
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_CONTROL, TOPIC_MISSION, TOPIC_ORTHO, TOPIC_STATUS
from shared import storage
from shared.geospatial_assets import convert_to_cog, metadata_path, preview_path
from shared.product_manifest import build_product_manifest, write_product_manifest
from shared.pipeline_params import (
    normalize_ai_backend,
    normalize_feature_type,
    normalize_matcher_type,
)
from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    DRONEGS_QUALIFICATION_POLICY_ID,
)
from shared.rtk_refinement import (
    assess_rtk_refinement_quality,
    inject_database_gravity_priors,
    inject_database_pose_priors,
    load_rtk_records,
)
from shared.gcp_control import (
    build_weighted_gcp_alignment,
    prepare_gcp_assets,
    write_transformed_reconstruction,
)
from shared.facade_selection import (
    deduplicate_identical_basenames,
    exclude_basename_ranges,
    select_facade_images,
)
from shared.facade_process import (
    FACADE_DRONEGS_IDENTITY_PARAMETERS,
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_PROCESS_PROFILE_ID,
    FACADE_QUALIFICATION_POLICY_ID,
    FACADE_QUALIFICATION_THRESHOLDS,
    apply_facade_process_profile,
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
    inspect_sparse_quality,
    merge_pipeline_params,
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
    build_gps_pair_graph,
    build_mapping_command,
    caspar_compatibility,
    choose_auto_fallback,
    choose_primary_engine,
    database_counts,
    parse_colmap_reference_file,
    write_pair_list,
)
from shared.json_io import atomic_write_json
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


def _remove_artifact_paths(artifact_paths):
    removed_paths = []
    for path in artifact_paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed_paths.append(path)
        elif os.path.exists(path):
            os.remove(path)
            removed_paths.append(path)
    return removed_paths


def _remove_rtk_dependent_artifacts(workspace_dir, dense_path):
    """Invalidate products whose geometry depends on the selected sparse model."""
    return _remove_artifact_paths(
        (
            dense_path,
            os.path.join(workspace_dir, "sparse_geo"),
            os.path.join(workspace_dir, "alignment_transform.json"),
            os.path.join(workspace_dir, "orthomosaic.tif"),
            os.path.join(workspace_dir, "orthomosaic.height.tif"),
            os.path.join(workspace_dir, "gcp_alignment_report.json"),
        )
    )


def _invalidate_artifact_paths(artifact_paths, vol_id, reason, artifact_kind):
    removed_paths = _remove_artifact_paths(artifact_paths)

    if removed_paths:
        report_mission_progress(
            vol_id,
            "PREPARING",
            3,
            log=(f"{reason} Removed {len(removed_paths)} stale {artifact_kind} artifacts."),
        )
    return removed_paths


def invalidate_pipeline_artifacts(
    clean_images_dir, workspace_dir, db_path, sparse_path, dense_path, geo_data_file, vol_id, reason
):
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
        os.path.join(workspace_dir, "facade_orthophoto.tif"),
        os.path.join(workspace_dir, "facade_orthophoto.height.tif"),
        os.path.join(workspace_dir, "facade_frame.json"),
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

    return _invalidate_artifact_paths(
        artifact_paths,
        vol_id,
        reason,
        "pipeline",
    )


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
    return _invalidate_artifact_paths(
        artifact_paths,
        vol_id,
        reason,
        "georeferencing",
    )


def normalize_gpu_index(raw_value, default="0"):
    normalized = str(raw_value if raw_value is not None else default).strip()
    if not normalized or normalized == "-1":
        normalized = default

    visible_devices = [token.strip() for token in os.getenv("CUDA_VISIBLE_DEVICES", "").split(",") if token.strip()]
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


@dataclass(frozen=True)
class PipelinePreparation:
    params: dict
    facade_mode: bool
    orthophoto_mode: str
    mission_s3_prefix: str
    clean_images_dir: str
    db_path: str
    sparse_path: str
    geo_data_file: str
    dense_path: str
    gcp_path: str | None
    gcp_accuracy_path: str | None
    facade_selection_report_path: str
    feature_type: str
    matcher_type: str
    feature_family: str
    resolved_matcher_type: str
    feature_gpu_index: str
    ba_gpu_index: str
    projected_crs_mode: str
    requested_projected_crs: str
    image_reader_camera_model: str
    image_reader_camera_params: str | None
    images: list[Path]


@dataclass(frozen=True)
class PipelineReconstruction:
    utm_crs: str | None
    alignment_transform_path: str | None
    ortho_only_ready: bool


@dataclass(frozen=True)
class PipelineRtkState:
    active_sparse_model_path: str
    ortho_only_ready: bool
    report_path: str


def prepare_colmap_pipeline_run(
    workspace_dir: str,
    input_dataset: str,
    vol_id: str,
    mission_params: dict,
) -> PipelinePreparation:
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
    facade_mode = apply_facade_process_profile(params, mission_params)
    orthophoto_mode = params["orthophoto_mode"]
    projected_crs_mode = str(params.get("projected_crs_mode", "auto-local")).strip().lower()
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
    report_mission_progress(
        vol_id, "DOWNLOADING_IMAGES", 3, log=f"Downloading input images from S3 prefix: {input_dataset}"
    )
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
    gcp_assets = (
        {"gcp_path": None, "accuracy_path": None, "changed": False}
        if facade_mode
        else prepare_gcp_assets(raw_image_dir, workspace_dir)
    )
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

    facade_selection_report_path = os.path.join(workspace_dir, "facade_selection_report.json")
    if facade_mode:
        raw_images = sorted(
            path
            for path in Path(raw_image_dir).rglob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        selection_mode = str(params["facade_selection_mode"]).lower()
        if selection_mode == "auto":
            target_yaw_value = str(params.get("facade_target_yaw_deg", "")).strip()
            images, selection_report = select_facade_images(
                raw_images,
                max_abs_pitch_deg=float(params["facade_max_abs_pitch_deg"]),
                min_pass_images=int(float(params["facade_min_pass_images"])),
                target_yaw_deg=(float(target_yaw_value) if target_yaw_value else None),
                yaw_tolerance_deg=float(params["facade_yaw_tolerance_deg"]),
                excluded_basename_ranges=params["facade_excluded_image_ranges"],
            )
        elif selection_mode == "all":
            images, duplicates = deduplicate_identical_basenames(raw_images)
            unique_image_count = len(images)
            images, exclusion_report = exclude_basename_ranges(
                images,
                params["facade_excluded_image_ranges"],
            )
            selection_report = {
                "schema_version": 2,
                "mode": "all",
                "input_images": len(raw_images),
                "unique_images": unique_image_count,
                "selected_images": len(images),
                "duplicate_basenames": duplicates,
                **exclusion_report,
            }
        else:
            raise ValueError(f"Unsupported facade_selection_mode: {selection_mode}")
        atomic_write_json(facade_selection_report_path, selection_report)
        position_sidecars = []
        report_mission_progress(
            vol_id,
            "PREPARING",
            5,
            log=(f"Facade selection retained {len(images)}/{len(raw_images)} images with mode={selection_mode}."),
            details={"event": "facade_selection", **selection_report},
        )
    else:
        images, position_sidecars = discover_input_assets(raw_image_dir)
    copy_candidates = images + position_sidecars
    report_mission_progress(
        vol_id,
        "COPYING_IMAGES",
        5,
        log=(
            f"Copying {len(images)} images and {len(position_sidecars)} DJI position sidecars to the clean workspace..."
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
    has_cached_reconstruction = any(os.path.exists(path) for path in (db_path, sparse_path, dense_path))
    if has_cached_reconstruction and (
        previous_cache_config is None
        or previous_cache_config.get("fingerprint") != requested_cache_config["fingerprint"]
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
            f"COLMAP reconstruction parameters changed ({', '.join(changed_parameters)}).",
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
            report_mission_progress(
                vol_id, "PREPARING", 3, log=f"Existing database compatible ({existing_type}). Resuming..."
            )

    save_colmap_cache_config(workspace_dir, requested_cache_config)
    return PipelinePreparation(
        params=params,
        facade_mode=facade_mode,
        orthophoto_mode=orthophoto_mode,
        mission_s3_prefix=mission_s3_prefix,
        clean_images_dir=clean_images_dir,
        db_path=db_path,
        sparse_path=sparse_path,
        geo_data_file=geo_data_file,
        dense_path=dense_path,
        gcp_path=gcp_path,
        gcp_accuracy_path=gcp_accuracy_path,
        facade_selection_report_path=facade_selection_report_path,
        feature_type=feature_type,
        matcher_type=matcher_type,
        feature_family=feature_family,
        resolved_matcher_type=resolved_matcher_type,
        feature_gpu_index=feature_gpu_index,
        ba_gpu_index=ba_gpu_index,
        projected_crs_mode=projected_crs_mode,
        requested_projected_crs=requested_projected_crs,
        image_reader_camera_model=image_reader_camera_model,
        image_reader_camera_params=image_reader_camera_params,
        images=images,
    )


@dataclass(frozen=True)
class SparseBootstrapState:
    utm_crs: str | None
    alignment_transform_path: str | None
    ortho_only_ready: bool
    gps_done: bool


def prepare_sparse_bootstrap(
    preparation: PipelinePreparation,
    workspace_dir: str,
    vol_id: str,
) -> SparseBootstrapState:
    facade_mode = preparation.facade_mode
    clean_images_dir = preparation.clean_images_dir
    geo_data_file = preparation.geo_data_file
    dense_path = preparation.dense_path
    projected_crs_mode = preparation.projected_crs_mode
    requested_projected_crs = preparation.requested_projected_crs

    # --- 2. GPS ---
    saved_projected_crs = read_saved_projected_crs(geo_data_file)
    saved_projection_policy = read_saved_projected_crs_policy(geo_data_file)
    gps_done = os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0 and bool(saved_projected_crs)
    projection_changed = False
    if gps_done and not saved_projection_policy:
        projection_changed = True
    elif gps_done and saved_projection_policy.get("policy") != projected_crs_mode:
        projection_changed = True
    elif gps_done and projected_crs_mode == "custom":
        projection_changed = saved_projected_crs.upper() != requested_projected_crs.upper()
    if projection_changed or (os.path.exists(geo_data_file) and not saved_projected_crs):
        invalidate_georeferencing_artifacts(
            workspace_dir,
            geo_data_file,
            vol_id,
            "The requested projected CRS policy changed.",
        )
        gps_done = False

    if facade_mode:
        utm_crs = None
        gps_done = False
        report_mission_progress(
            vol_id,
            "GPS_EXTRACTION",
            12,
            log="Facade mode uses no projected CRS and no absolute camera-position alignment.",
        )
    elif gps_done:
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

    gps_done = not facade_mode and os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0
    sanitize_exif_for_colmap(clean_images_dir, vol_id, report_mission_progress)

    align_tf = os.path.join(workspace_dir, "alignment_transform.json")
    align_tf = align_tf if os.path.exists(align_tf) else None
    dense_sparse_ready = dense_sparse_model_ready(dense_path)

    # Gaussian Splatting only needs dense/sparse + undistorted images.
    gs_ready = dense_sparse_ready and os.path.isdir(os.path.join(dense_path, "images"))
    ortho_only_ready = gs_ready
    if ortho_only_ready:
        report_mission_progress(
            vol_id,
            "PREPARING",
            13,
            log="Existing undistorted images found. Skipping SfM and rebuilding Gaussian Splatting orthomosaic only.",
        )

    return SparseBootstrapState(
        utm_crs=utm_crs,
        alignment_transform_path=align_tf,
        ortho_only_ready=ortho_only_ready,
        gps_done=gps_done,
    )


def reconstruct_colmap_sparse(
    preparation: PipelinePreparation,
    workspace_dir: str,
    vol_id: str,
) -> PipelineReconstruction:
    params = preparation.params
    facade_mode = preparation.facade_mode
    clean_images_dir = preparation.clean_images_dir
    db_path = preparation.db_path
    sparse_path = preparation.sparse_path
    geo_data_file = preparation.geo_data_file
    feature_type = preparation.feature_type
    feature_family = preparation.feature_family
    resolved_matcher_type = preparation.resolved_matcher_type
    feature_gpu_index = preparation.feature_gpu_index
    ba_gpu_index = preparation.ba_gpu_index
    image_reader_camera_model = preparation.image_reader_camera_model
    image_reader_camera_params = preparation.image_reader_camera_params
    images = preparation.images

    bootstrap = prepare_sparse_bootstrap(
        preparation,
        workspace_dir,
        vol_id,
    )
    utm_crs = bootstrap.utm_crs
    align_tf = bootstrap.alignment_transform_path
    ortho_only_ready = bootstrap.ortho_only_ready
    gps_done = bootstrap.gps_done

    # --- 3. SfM: Feature Extraction ---
    sparse_done = os.path.exists(os.path.join(sparse_path, "0", "cameras.bin")) or os.path.exists(
        os.path.join(sparse_path, "0", "cameras.txt")
    )

    if not sparse_done and not ortho_only_ready:
        # Build feature extraction command based on the selected extractor.
        feature_num_threads = str(params.get("feature_num_threads", "-1"))
        feat_cmd = [
            "colmap",
            "feature_extractor",
            "--database_path",
            db_path,
            "--image_path",
            clean_images_dir,
            "--ImageReader.single_camera",
            "1",
            "--ImageReader.camera_model",
            image_reader_camera_model,
            "--FeatureExtraction.num_threads",
            feature_num_threads,
        ]
        if image_reader_camera_params:
            feat_cmd += [
                "--ImageReader.camera_params",
                image_reader_camera_params,
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
                "--FeatureExtraction.type",
                params["feature_type"],
                "--FeatureExtraction.use_gpu",
                "1",
                "--FeatureExtraction.gpu_index",
                feature_gpu_index,
                "--FeatureExtraction.max_image_size",
                str(effective_feature_max_image_size),
                "--AlikedExtraction.max_num_features",
                params["feature_max_num_features"],
            ]
            model_dir = os.getenv(
                "COLMAP_MODEL_DIR",
                "/usr/local/share/colmap/models",
            )
            model_filename = "aliked-n32.onnx" if params["feature_type"] == "ALIKED_N32" else "aliked-n16rot.onnx"
            model_option = (
                "--AlikedExtraction.n32_model_path"
                if params["feature_type"] == "ALIKED_N32"
                else "--AlikedExtraction.n16rot_model_path"
            )
            feat_cmd += [model_option, os.path.join(model_dir, model_filename)]
        else:
            feat_cmd += [
                "--FeatureExtraction.type",
                feature_type,
                "--FeatureExtraction.use_gpu",
                "1",
                "--FeatureExtraction.gpu_index",
                feature_gpu_index,
                "--FeatureExtraction.max_image_size",
                params["feature_max_image_size"],
                "--SiftExtraction.max_num_features",
                params["feature_max_num_features"],
                "--SiftExtraction.first_octave",
                str(params["sift_first_octave"]),
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
                "--FeatureMatching.max_num_matches",
                str(params["feature_max_num_matches"]),
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
                "--FeatureMatching.max_num_matches",
                str(params["feature_max_num_matches"]),
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
                "--FeatureMatching.max_num_matches",
                str(params["feature_max_num_matches"]),
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
        if facade_mode and matching_strategy == "spatial":
            # GPS proximity connects different angle passes, while capture
            # order is still the strongest cue along each individual
            # sweep. Running both matchers is inexpensive relative to a
            # failed facade solve and COLMAP skips pairs already verified.
            sequential_cmd = [
                "colmap",
                "sequential_matcher",
                "--database_path",
                db_path,
                "--SequentialMatching.overlap",
                "15",
                "--FeatureMatching.type",
                resolved_matcher_type,
                "--FeatureMatching.use_gpu",
                "1",
                "--FeatureMatching.gpu_index",
                feature_gpu_index,
                "--FeatureMatching.guided_matching",
                "1" if params.get("guided_matching", False) else "0",
                "--FeatureMatching.max_num_matches",
                str(params["feature_max_num_matches"]),
            ]
            sequential_cmd += matching_model_options
            run_command(
                sequential_cmd,
                vol_id,
                "MATCHING",
                32,
                report_mission_progress,
                ensure_not_cancelled,
            )
        match_counts = database_counts(db_path)
        report_mission_progress(
            vol_id,
            "MATCHING",
            34,
            log=(f"Verified {match_counts['two_view_geometries']} image pairs for {match_counts['images']} images."),
            details={"event": "matching_complete", **match_counts},
        )

        gravity_available = False
        if bool(params.get("imu_gravity_enabled", False)):
            orientation_records = load_rtk_records(clean_images_dir)
            gravity_report = inject_database_gravity_priors(
                db_path,
                orientation_records,
            )
            gravity_available = bool(gravity_report["use_in_global_rotation_averaging"])
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
            run_command(
                [
                    "colmap",
                    "view_graph_calibrator",
                    "--database_path",
                    db_path,
                ],
                vol_id,
                "CALIBRATING",
                38,
                report_mission_progress,
                ensure_not_cancelled,
            )

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
        maximum_reprojection_error = float(params["maximum_mean_reprojection_error_px"])
        minimum_track_length = float(params["minimum_median_track_length"])

        def passes_sparse_quality(quality):
            reprojection_error = quality["mean_reprojection_error_px"]
            track_length = quality["median_track_length"]
            return (
                quality["registered_images"] >= minimum_registered_images
                and quality["points3D"] > 0
                and reprojection_error is not None
                and reprojection_error <= maximum_reprojection_error
                and track_length is not None
                and track_length >= minimum_track_length
            )

        def remaining_mapping_budget():
            remaining = mapping_timeout - (time.monotonic() - mapping_started_at)
            if remaining <= 0:
                raise TimeoutError(f"The shared {mapping_timeout:.0f}s mapping budget is exhausted.")
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
                global_ceres_iterations=int(float(params["global_mapper_ceres_iterations"])),
                global_skip_retriangulation=bool(params.get("global_mapper_skip_retriangulation", True)),
                global_random_seed=int(float(params["global_mapper_random_seed"])),
                global_ba_min_track_length=int(float(params["global_mapper_ba_min_track_length"])),
                global_tri_complete_max_reproj_error=float(params["global_mapper_tri_complete_max_reproj_error"]),
                global_tri_merge_max_reproj_error=float(params["global_mapper_tri_merge_max_reproj_error"]),
                global_tri_min_angle=float(params["global_mapper_tri_min_angle"]),
                global_use_gravity=gravity_available,
            )
            report_mission_progress(
                vol_id,
                "MAPPING",
                progress,
                log=(f"Starting alignment engine={engine} with a {engine_timeout:.0f}s remaining shared time budget."),
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

        primary_engine = choose_primary_engine(
            requested_engine,
            facade=facade_mode,
        )
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
                    f"Primary {primary_engine.upper()} attempt failed within its bounded budget: "
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
        primary_usable = primary_error is None and passes_sparse_quality(quality)
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
                "maximum_mean_reprojection_error_px": (maximum_reprojection_error),
                "minimum_median_track_length": minimum_track_length,
                **quality,
                "accepted": primary_usable,
            },
        )

        if not primary_usable and requested_engine == "auto":
            caspar_supported, camera_models = caspar_compatibility(db_path)
            fallback_engine = "ceres" if primary_engine == "caspar" else choose_auto_fallback(camera_models)
            report_mission_progress(
                vol_id,
                "MAPPING",
                47,
                log=(
                    f"{primary_engine.upper()} quality gate failed. Reusing the existing features "
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

    return PipelineReconstruction(
        utm_crs=utm_crs,
        alignment_transform_path=align_tf,
        ortho_only_ready=ortho_only_ready,
    )


def refine_colmap_rtk(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    workspace_dir: str,
    vol_id: str,
) -> PipelineRtkState:
    params = preparation.params
    clean_images_dir = preparation.clean_images_dir
    db_path = preparation.db_path
    dense_path = preparation.dense_path
    sparse_path = preparation.sparse_path
    ba_gpu_index = preparation.ba_gpu_index
    ortho_only_ready = reconstruction.ortho_only_ready

    # --- 6b. Optional covariance-aware RTK/PPK refinement ---
    base_sparse_model_path = os.path.join(sparse_path, "0")
    rtk_sparse_model_path = os.path.join(workspace_dir, "sparse_rtk")
    rtk_report_path = os.path.join(workspace_dir, "rtk_prior_report.json")
    rtk_refinement_enabled = bool(params.get("rtk_refinement_enabled", True))
    active_sparse_model_path = base_sparse_model_path

    def evaluate_rtk_candidate():
        return assess_rtk_refinement_quality(
            inspect_sparse_quality(base_sparse_model_path),
            inspect_sparse_quality(rtk_sparse_model_path),
            minimum_point_ratio=float(params["rtk_minimum_point_ratio"]),
            maximum_reprojection_degradation_px=float(params["rtk_maximum_reprojection_degradation_px"]),
            maximum_track_length_loss_ratio=float(params["rtk_maximum_track_length_loss_ratio"]),
            maximum_focal_length_change_ratio=float(params["rtk_maximum_focal_length_change_ratio"]),
        )

    if rtk_refinement_enabled and os.path.exists(os.path.join(base_sparse_model_path, "cameras.bin")):
        if os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin")):
            quality_gate = evaluate_rtk_candidate()
            cached_report = {"schema_version": 1}
            try:
                with open(rtk_report_path, encoding="utf-8") as handle:
                    cached_report.update(json.load(handle))
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                pass
            cached_report["quality_gate"] = quality_gate
            previous_selected_model = cached_report.get("selected_model")
            selected_model = "rtk_candidate" if quality_gate["accepted"] else "visual_baseline"
            cached_report["selected_model"] = selected_model
            cached_report["status"] = "completed" if quality_gate["accepted"] else "rejected-quality-gate"
            atomic_write_json(rtk_report_path, cached_report)
            if quality_gate["accepted"]:
                active_sparse_model_path = rtk_sparse_model_path
            if (previous_selected_model is not None and previous_selected_model != selected_model) or (
                previous_selected_model is None and selected_model == "visual_baseline"
            ):
                # Cached dense/render products may have been derived from
                # the previously auto-promoted RTK model. Force a rebuild
                # when the gate changes (or first rejects) the selection.
                ortho_only_ready = False
                _remove_rtk_dependent_artifacts(workspace_dir, dense_path)
            report_mission_progress(
                vol_id,
                "RTK_REFINEMENT",
                57,
                log=(
                    "Reusing the quality-gated covariance-aware RTK sparse model."
                    if quality_gate["accepted"]
                    else "Cached RTK candidate failed the comparison gate; retaining the visual baseline."
                ),
                details={
                    "event": "rtk_refinement_reuse_gate",
                    "quality_gate": quality_gate,
                },
            )
        elif not ortho_only_ready:
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
                if not os.path.exists(os.path.join(rtk_sparse_model_path, "cameras.bin")):
                    raise RuntimeError("pose_prior_mapper did not write a usable RTK model")
                quality_gate = evaluate_rtk_candidate()
                rtk_report.update(
                    {
                        "status": ("completed" if quality_gate["accepted"] else "rejected-quality-gate"),
                        "elapsed_seconds": time.monotonic() - rtk_started_at,
                        "iterations": rtk_iterations,
                        "timeout_seconds": rtk_timeout,
                        "ba_backend": "CERES_GPU",
                        "robust_loss": "cauchy",
                        "robust_loss_scale": rtk_loss_scale,
                        "quality_gate": quality_gate,
                        "selected_model": ("rtk_candidate" if quality_gate["accepted"] else "visual_baseline"),
                    }
                )
                atomic_write_json(rtk_report_path, rtk_report)
                if quality_gate["accepted"]:
                    active_sparse_model_path = rtk_sparse_model_path
                    _remove_rtk_dependent_artifacts(workspace_dir, dense_path)
                report_mission_progress(
                    vol_id,
                    "RTK_REFINEMENT",
                    58,
                    log=(
                        "RTK pose refinement passed the comparison gate; downstream "
                        "processing will use the constrained model."
                        if quality_gate["accepted"]
                        else "RTK candidate degraded visual sparse metrics and was rejected; downstream processing retains the baseline model."
                    ),
                    details={
                        "event": (
                            "rtk_refinement_completed" if quality_gate["accepted"] else "rtk_refinement_rejected"
                        ),
                        **rtk_report,
                    },
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
                    "selected_model": "visual_baseline",
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

    return PipelineRtkState(
        active_sparse_model_path=active_sparse_model_path,
        ortho_only_ready=ortho_only_ready,
        report_path=rtk_report_path,
    )


@dataclass(frozen=True)
class PipelineAlignmentState:
    alignment_transform_path: str | None


def undistort_and_align_colmap(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    workspace_dir: str,
    vol_id: str,
) -> PipelineAlignmentState:
    params = preparation.params
    facade_mode = preparation.facade_mode
    clean_images_dir = preparation.clean_images_dir
    dense_path = preparation.dense_path
    geo_data_file = preparation.geo_data_file
    gcp_path = preparation.gcp_path
    gcp_accuracy_path = preparation.gcp_accuracy_path
    utm_crs = reconstruction.utm_crs
    align_tf = reconstruction.alignment_transform_path
    ortho_only_ready = rtk_state.ortho_only_ready
    active_sparse_model_path = rtk_state.active_sparse_model_path

    # --- 7. Undistort images for Gaussian Splatting ---
    # GS only needs the undistorted images + dense/sparse model.
    if ortho_only_ready:
        report_mission_progress(vol_id, "UNDISTORT", 75, log="Undistorted images found. Skipping undistortion.")
    else:
        if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
            run_command(
                [
                    "colmap",
                    "image_undistorter",
                    "--image_path",
                    clean_images_dir,
                    "--input_path",
                    active_sparse_model_path,
                    "--output_path",
                    dense_path,
                    "--max_image_size",
                    params["mvs_max_image_size"],
                    "--num_threads",
                    params["mvs_num_threads"],
                ],
                vol_id,
                "UNDISTORT",
                70,
                report_mission_progress,
                ensure_not_cancelled,
            )
        else:
            report_mission_progress(
                vol_id, "UNDISTORT", 70, log="Undistorted images and fusion.cfg found. Skipping undistortion."
            )

        n_undistorted = (
            len(os.listdir(os.path.join(dense_path, "images")))
            if os.path.isdir(os.path.join(dense_path, "images"))
            else 0
        )
        report_mission_progress(
            vol_id,
            "UNDISTORT",
            90,
            log=f"Using {n_undistorted} undistorted images for Gaussian Splatting.",
        )

    # --- 8. Geo-alignment ---
    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")

    def ensure_alignment_transform():
        nonlocal align_tf
        gcp_adjustment_enabled = bool(params.get("gcp_adjustment_enabled", False))
        transform_file = os.path.join(
            workspace_dir,
            "alignment_transform.json",
        )
        if gcp_adjustment_enabled:
            if not gcp_path:
                raise RuntimeError("Weighted GCP adjustment is enabled but the dataset does not contain gcp_list.txt")
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
                default_horizontal_accuracy_m=float(params["gcp_horizontal_accuracy_m"]),
                default_vertical_accuracy_m=float(params["gcp_vertical_accuracy_m"]),
                default_image_accuracy_px=float(params["gcp_image_accuracy_px"]),
                robust_loss_scale=float(params["gcp_robust_loss_scale"]),
                require_checkpoints=bool(params["gcp_require_checkpoints"]),
                minimum_checkpoint_count=int(params["gcp_min_checkpoint_count"]),
                maximum_checkpoint_horizontal_rmse_m=float(params["gcp_max_checkpoint_horizontal_rmse_m"]),
                maximum_checkpoint_vertical_rmse_m=float(params["gcp_max_checkpoint_vertical_rmse_m"]),
                maximum_checkpoint_normalized_error_sigma=float(params["gcp_max_checkpoint_normalized_error_sigma"]),
                minimum_adjustment_baseline_m=float(params["gcp_min_adjustment_baseline_m"]),
            )
            gcp_report_file = os.path.join(workspace_dir, "gcp_alignment_report.json")
            atomic_write_json(gcp_report_file, gcp_report)
            quality_gate = gcp_report["quality_gate"]
            if not quality_gate["accepted"]:
                failed_checks = ", ".join(check["name"] for check in quality_gate["checks"] if not check["passed"])
                raise RuntimeError("GCP alignment rejected by the promotion gate: " + failed_checks)
            from shared.geo_alignment import write_alignment_transform

            write_alignment_transform(transform_file, transform)
            write_transformed_reconstruction(
                active_sparse_model_path,
                sparse_geo_path,
                transform,
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
                    f"weighted RMSE={fit['weighted_rmse']:.2f}σ, "
                    f"status={quality_gate['status']})."
                ),
                details={
                    "event": "gcp_alignment_completed",
                    "adjustment_points": gcp_report["adjustment_points"],
                    "checkpoint_points": gcp_report["checkpoint_points"],
                    "quality_gate": quality_gate,
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
                if previous_alignment.get("fit", {}).get("source") == "covariance_weighted_gcp":
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
                raise RuntimeError(f"Cannot inspect cached alignment transform: {error}") from error

        if stale_gcp_alignment:
            shutil.rmtree(sparse_geo_path, ignore_errors=True)

        if not (os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0):
            return None

        os.makedirs(sparse_geo_path, exist_ok=True)
        align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin"))
        if not align_done:
            run_command(
                [
                    "colmap",
                    "model_aligner",
                    "--input_path",
                    active_sparse_model_path,
                    "--output_path",
                    sparse_geo_path,
                    "--ref_images_path",
                    geo_data_file,
                    "--ref_is_gps",
                    "0",
                    "--alignment_max_error",
                    str(params["alignment_max_error"]),
                ],
                vol_id,
                "ALIGNING",
                93,
                report_mission_progress,
                ensure_not_cancelled,
            )

        if align_tf and os.path.exists(align_tf):
            return align_tf

        report_mission_progress(
            vol_id, "ALIGNING", 94, log="Computing sparse-model alignment transform for orthorectification..."
        )
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
            report_mission_progress(
                vol_id,
                "ALIGNING",
                94,
                log=f"Failed to compute alignment transform ({e}); using raw COLMAP coordinates.",
            )
            return None

    if facade_mode:
        align_tf = None
        report_mission_progress(
            vol_id,
            "ALIGNING",
            94,
            log="Skipping geographic alignment; the dominant facade plane defines the local frame.",
        )
    else:
        ensure_alignment_transform()

    return PipelineAlignmentState(alignment_transform_path=align_tf)


@dataclass(frozen=True)
class PipelineGaussianState:
    ortho_file: str
    result: dict
    durable_checkpoint_dir: str
    checkpoint_s3_prefix: str
    profile_id: str
    qualification_policy_id: str


def run_gaussian_product(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    alignment_state: PipelineAlignmentState,
    workspace_dir: str,
    vol_id: str,
) -> PipelineGaussianState:
    params = preparation.params
    facade_mode = preparation.facade_mode
    orthophoto_mode = preparation.orthophoto_mode
    mission_s3_prefix = preparation.mission_s3_prefix
    dense_path = preparation.dense_path
    utm_crs = reconstruction.utm_crs
    align_tf = alignment_state.alignment_transform_path

    # --- 9. Gaussian Splatting Orthomosaic ---
    ortho_file = os.path.join(
        workspace_dir,
        "facade_orthophoto.tif" if facade_mode else "orthomosaic.tif",
    )

    align_tf_path = os.path.join(workspace_dir, "alignment_transform.json")
    if not facade_mode and os.path.exists(align_tf_path):
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
            image_files = (
                [f for f in os.listdir(images_dir) if f.lower().endswith((".jpg", ".jpeg", ".png"))]
                if os.path.isdir(images_dir)
                else []
            )
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
            gs_data_factor = choose_dronegs_data_factor(max_dim, max_training_width)
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
        gs_qualification_policy_id = str(
            params.get(
                "gs_qualification_policy",
                DRONEGS_QUALIFICATION_POLICY_ID,
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
        gs_sh_degree_interval = int(params.get("gs_sh_degree_interval", 1_000))
        gs_topology_cooldown = int(params.get("gs_topology_cooldown", 1_000))
        gs_photometric_finish = int(params.get("gs_photometric_finish", 1_000))
        gs_photometric_mse_percent = int(params.get("gs_photometric_mse_percent", 100))
        gs_checkpoint_every = int(params.get("gs_checkpoint_every", 2_000))
        gs_test_every = int(params.get("gs_test_every", 8))
        gs_test_split = str(params.get("gs_test_split", "modulo"))
        gs_test_guard_percent = int(params.get("gs_test_guard_percent", 0))
        gs_canary_min_psnr = float(
            params.get(
                ("facade_canary_min_psnr" if facade_mode else "gs_canary_min_psnr"),
                DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr,
            )
        )
        gs_canary_min_ssim = float(
            params.get(
                ("facade_canary_min_ssim" if facade_mode else "gs_canary_min_ssim"),
                DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim,
            )
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
        expected_profile_values = None
        if gs_profile_id in {
            DRONEGS_PRODUCTION_PROFILE_V1.profile_id,
            FACADE_DRONEGS_PROFILE_ID,
        }:
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
                "photometric_mse_percent": (gs_photometric_mse_percent),
                "checkpoint_every": gs_checkpoint_every,
                "test_every": gs_test_every,
                "test_split": gs_test_split,
                "test_guard_percent": gs_test_guard_percent,
            }
            if gs_profile_id == FACADE_DRONEGS_PROFILE_ID:
                expected_profile_values = dict(FACADE_DRONEGS_IDENTITY_PARAMETERS)
            else:
                expected_profile_values = {
                    name: getattr(DRONEGS_PRODUCTION_PROFILE_V1, name) for name in profile_values
                }
            if profile_values != expected_profile_values:
                gs_profile_id = "custom"
                report_mission_progress(
                    vol_id,
                    "GAUSS",
                    94,
                    log=(
                        "DroneGS expert overrides detected; the run is recorded as custom instead of its named profile."
                    ),
                )

        expected_qualification = None
        if gs_qualification_policy_id == DRONEGS_QUALIFICATION_POLICY_ID:
            expected_qualification = {
                "canary_min_psnr": (DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr),
                "canary_min_ssim": (DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim),
            }
        elif gs_qualification_policy_id == FACADE_QUALIFICATION_POLICY_ID:
            expected_qualification = dict(FACADE_QUALIFICATION_THRESHOLDS)
        if expected_qualification is not None and expected_qualification != {
            "canary_min_psnr": gs_canary_min_psnr,
            "canary_min_ssim": gs_canary_min_ssim,
        }:
            gs_qualification_policy_id = "custom"
            report_mission_progress(
                vol_id,
                "GAUSS",
                94,
                log=(
                    "DroneGS canary thresholds differ from qualification "
                    "policy V1; training recipe identity is preserved and "
                    "qualification policy is recorded as custom."
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
        checkpoint_s3_prefix = f"{mission_s3_prefix}/gaussian-checkpoints"
        if not any(path.is_file() for path in Path(durable_checkpoint_dir).rglob("*")):
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
                        log=(f"Restored {restored_count} durable DroneGS artifacts from S3."),
                    )
            except Exception as restore_error:
                report_mission_progress(
                    vol_id,
                    "GAUSS",
                    94,
                    log=(f"No remote DroneGS recovery state restored: {restore_error}"),
                )

        def persist_dronegs_checkpoint(checkpoint_path, iteration):
            relative = checkpoint_path.resolve().relative_to(Path(durable_checkpoint_dir).resolve())
            s3_key = f"{checkpoint_s3_prefix}/{relative.as_posix()}"
            try:
                storage.upload_file(checkpoint_path, s3_key)
                report_mission_progress(
                    vol_id,
                    "GAUSS",
                    95,
                    log=(f"Durable DroneGS checkpoint synced at iteration {iteration}."),
                )
            except Exception as sync_error:
                report_mission_progress(
                    vol_id,
                    "GAUSS",
                    95,
                    log=(f"DroneGS checkpoint remains locally durable; S3 sync failed: {sync_error}"),
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
            ortho_mip_filter_variance=float(params.get("gs_ortho_mip_filter_variance", 0.03)),
            ortho_mip_filter_compensation=bool(params.get("gs_ortho_mip_filter_compensation", True)),
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
            dronegs_qualification_policy_id=(gs_qualification_policy_id),
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
            render_mode=orthophoto_mode,
            facade_scale_mode=str(params["facade_scale_mode"]),
            facade_meters_per_model_unit=float(params["facade_meters_per_model_unit"]),
            facade_frame_report=os.path.join(workspace_dir, "facade_frame.json"),
            facade_texture_max_incidence_deg=float(params["facade_texture_max_incidence_deg"]),
            facade_depth_iqr_multiplier=float(params["facade_depth_iqr_multiplier"]),
            facade_seed_max_reprojection_error=float(params["facade_seed_max_reprojection_error"]),
            facade_seed_min_track_length=int(params["facade_seed_min_track_length"]),
        )
        report_mission_progress(
            vol_id,
            "GAUSS",
            100,
            log=f"Gaussian Splatting {'facade orthophoto' if facade_mode else 'orthomosaic'} complete: "
            f"{result['width']}x{result['height']}px, "
            f"{result['n_gaussians']} Gaussians, "
            f"pixel size={ortho_resolution} {result['gsd_units']}",
        )
    except Exception as e:
        _tb.print_exc()
        report_mission_progress(vol_id, "ORTHO", 95, log=f"Gaussian Splatting ortho failed: {e}")
        raise

    return PipelineGaussianState(
        ortho_file=ortho_file,
        result=result,
        durable_checkpoint_dir=durable_checkpoint_dir,
        checkpoint_s3_prefix=checkpoint_s3_prefix,
        profile_id=gs_profile_id,
        qualification_policy_id=gs_qualification_policy_id,
    )


@dataclass(frozen=True)
class PipelinePublicationState:
    ortho_s3_key: str
    gaussian_upload_complete: bool


def publish_colmap_products(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    rtk_state: PipelineRtkState,
    alignment_state: PipelineAlignmentState,
    gaussian_state: PipelineGaussianState,
    workspace_dir: str,
    vol_id: str,
) -> PipelinePublicationState:
    params = preparation.params
    facade_mode = preparation.facade_mode
    mission_s3_prefix = preparation.mission_s3_prefix
    db_path = preparation.db_path
    sparse_path = preparation.sparse_path
    geo_data_file = preparation.geo_data_file
    dense_path = preparation.dense_path
    facade_selection_report_path = preparation.facade_selection_report_path
    utm_crs = reconstruction.utm_crs
    active_sparse_model_path = rtk_state.active_sparse_model_path
    rtk_report_path = rtk_state.report_path
    align_tf = alignment_state.alignment_transform_path
    ortho_file = gaussian_state.ortho_file
    result = gaussian_state.result
    durable_checkpoint_dir = gaussian_state.durable_checkpoint_dir
    gs_profile_id = gaussian_state.profile_id
    gs_qualification_policy_id = gaussian_state.qualification_policy_id
    sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
    gaussian_upload_complete = False

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
    product_stem = "facade_orthophoto" if facade_mode else "orthomosaic"
    ortho_s3_key = f"{mission_s3_prefix}/{product_stem}.tif"
    if not os.path.isfile(ortho_file):
        raise FileNotFoundError(f"Required orthomosaic artifact is missing: {ortho_file}")

    # The final raster is a required, independently verifiable product.
    # Never announce DONE or start downstream processing unless conversion,
    # upload and remote integrity verification all succeeded.
    convert_to_cog(ortho_file)
    height_tif = result["height_file"]
    if not os.path.isfile(height_tif):
        raise FileNotFoundError(f"Required DSM artifact is missing: {height_tif}")
    convert_to_cog(height_tif)

    final_ply = result["final_ply"]
    if not os.path.isfile(final_ply):
        raise FileNotFoundError(f"Required reusable Gaussian artifact is missing: {final_ply}")
    trainer_manifests = sorted(Path(result["checkpoint_dir"]).rglob("trainer_run.json"))
    if not trainer_manifests:
        raise FileNotFoundError("Required DroneGS training manifest is missing")
    qualification_manifests = sorted(Path(result["checkpoint_dir"]).rglob("canary_result.json"))
    if len(qualification_manifests) != len(trainer_manifests):
        raise FileNotFoundError("Every reusable DroneGS model requires a canary qualification manifest")

    gcp_enabled = bool(params.get("gcp_adjustment_enabled", False))
    gcp_report_file = os.path.join(workspace_dir, "gcp_alignment_report.json")
    required_reports = {
        "rtk_prior_report": (rtk_report_path if os.path.isfile(rtk_report_path) else None),
        "imu_gravity_report": os.path.join(workspace_dir, "imu_gravity_report.json"),
        "alignment_transform": align_tf,
        "gcp_alignment_report": (gcp_report_file if gcp_enabled else None),
        "facade_frame_report": (result.get("facade_frame_report") if facade_mode else None),
        "facade_selection_report": (facade_selection_report_path if facade_mode else None),
    }
    product_manifest_path = os.path.join(workspace_dir, "product_manifest.json")
    product_manifest = build_product_manifest(
        mission_id=vol_id,
        projected_crs=("LOCAL_FACADE" if facade_mode else utm_crs),
        parameters={
            "pipeline": params,
            "effective_product_profile_id": (FACADE_PROCESS_PROFILE_ID if facade_mode else "AERIAL_MAP"),
            "effective_training_profile_id": gs_profile_id,
            "effective_qualification_policy_id": (gs_qualification_policy_id),
            "renderer": {
                "render_mode": result["render_mode"],
                "coordinate_system": result["coordinate_system"],
                "width": result["width"],
                "height": result["height"],
                "pixel_size": result["gsd"],
                "pixel_size_units": result["gsd_units"],
                "scale_source": result["scale_source"],
                "meters_per_model_unit": result["meters_per_model_unit"],
                **({"gsd_m": result["gsd"]} if not facade_mode else {}),
                "raster_extent": result["raster_extent"],
                "projected_extent": result["projected_extent"],
                "vertical_reference": result["vertical_reference"],
                "vertical_offset_m": result["vertical_offset_m"],
                "gaussians": result["n_gaussians"],
                "renderer_contract": result["renderer_contract"],
                "cupy_version": result["cupy_version"],
                "mip_filter_variance": result["ortho_mip_filter_variance"],
                "mip_filter_compensation": result["ortho_mip_filter_compensation"],
                "sh_frame_policy": (
                    "colmap-view-direction-local-facade-v1" if facade_mode else "inverse-sim3-view-direction-v1"
                ),
            },
        },
        products={
            ("facade_orthophoto_cog" if facade_mode else "orthomosaic_cog"): ortho_file,
            ("facade_orthophoto_metadata" if facade_mode else "orthomosaic_metadata"): metadata_path(ortho_file),
            ("facade_orthophoto_preview" if facade_mode else "orthomosaic_preview"): preview_path(ortho_file),
            ("facade_depth_cog" if facade_mode else "dsm_cog"): height_tif,
            ("facade_depth_metadata" if facade_mode else "dsm_metadata"): metadata_path(height_tif),
            ("facade_depth_preview" if facade_mode else "dsm_preview"): preview_path(height_tif),
            "gaussian_model": final_ply,
        },
        sparse_model_path=active_sparse_model_path,
        reports=required_reports,
        trainer_manifests=trainer_manifests,
        qualification_manifests=qualification_manifests,
        git_revision=os.getenv("DRONEAI_GIT_REVISION"),
        software_components={
            "pipeline": Path(__file__),
            "product_manifest": ROOT_DIR / "shared" / "product_manifest.py",
            "rtk_refinement": ROOT_DIR / "shared" / "rtk_refinement.py",
            "gcp_control": ROOT_DIR / "shared" / "gcp_control.py",
            "facade_selection": ROOT_DIR / "shared" / "facade_selection.py",
            "facade_process": ROOT_DIR / "shared" / "facade_process.py",
            "facade_frame": (ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "facade_frame.py"),
            "ortho_generator": (ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "generate_gaussian_orthophoto.py"),
            "ortho_renderer": (ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "ortho_renderer.py"),
            "cuda_rasterizer": (ROOT_DIR / "app1-colmap" / "gaussian_ortho" / "cuda_rasterizer.py"),
        },
    )
    write_product_manifest(product_manifest_path, product_manifest)

    storage.upload_verified_file(ortho_file, ortho_s3_key)
    storage.upload_verified_file(
        metadata_path(ortho_file),
        f"{ortho_s3_key}.cog.json",
    )
    storage.upload_verified_file(
        preview_path(ortho_file),
        f"{mission_s3_prefix}/{product_stem}.preview.webp",
    )
    upload_count += 3
    height_key = f"{mission_s3_prefix}/{product_stem}.height.tif"
    for local_path, remote_key in (
        (height_tif, height_key),
        (metadata_path(height_tif), f"{height_key}.cog.json"),
        (
            preview_path(height_tif),
            f"{mission_s3_prefix}/{product_stem}.height.preview.webp",
        ),
        (final_ply, f"{mission_s3_prefix}/gaussian/final.ply"),
    ):
        storage.upload_verified_file(local_path, remote_key)
        upload_count += 1
    checkpoint_root_path = Path(result["checkpoint_dir"]).resolve()
    for required_manifest in [
        *trainer_manifests,
        *qualification_manifests,
    ]:
        relative = required_manifest.resolve().relative_to(checkpoint_root_path)
        storage.upload_verified_file(
            required_manifest,
            f"{mission_s3_prefix}/gaussian/{relative.as_posix()}",
        )
        upload_count += 1

    report_remote_names = {
        "rtk_prior_report": "rtk_prior_report.json",
        "imu_gravity_report": "imu_gravity_report.json",
        "alignment_transform": "alignment_transform.json",
        "gcp_alignment_report": "gcp_alignment_report.json",
        "facade_frame_report": "facade_frame.json",
        "facade_selection_report": "facade_selection_report.json",
    }
    for report_name, report_path in required_reports.items():
        if report_path is None or not os.path.isfile(report_path):
            continue
        storage.upload_verified_file(
            report_path,
            f"{mission_s3_prefix}/{report_remote_names[report_name]}",
        )
        upload_count += 1

    if gcp_enabled:
        if not align_tf or not os.path.isfile(align_tf):
            raise FileNotFoundError("GCP mission is missing its required alignment transform")
        if not os.path.isfile(gcp_report_file):
            raise FileNotFoundError("GCP mission is missing its required alignment report")
        for required_name in ("cameras.bin", "images.bin", "points3D.bin"):
            required_sparse_file = os.path.join(sparse_geo_path, required_name)
            if not os.path.isfile(required_sparse_file):
                raise FileNotFoundError(f"GCP mission is missing sparse_geo/{required_name}")
            storage.upload_verified_file(
                required_sparse_file,
                f"{mission_s3_prefix}/colmap/sparse_geo/{required_name}",
            )
            upload_count += 1

    storage.upload_verified_file(
        product_manifest_path,
        f"{mission_s3_prefix}/product_manifest.json",
    )
    upload_count += 1
    report_mission_progress(
        vol_id,
        "UPLOADING",
        92,
        log="Verified COG, raster metadata and bounded preview uploaded",
    )

    # Remaining recovery/debug products are useful but do not invalidate a
    # successfully published orthomosaic when one of them cannot be copied.
    try:
        # 2. Remaining geo data (reports above are hash-verified)
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
        if not gcp_enabled and os.path.isdir(sparse_geo_path):
            n = storage.upload_directory(sparse_geo_path, f"{mission_s3_prefix}/colmap/sparse_geo/")
            upload_count += n

        report_mission_progress(vol_id, "UPLOADING", 94, log="COLMAP sparse models uploaded")

        # 4. Dense reconstruction (undistorted model + images)
        if os.path.isdir(dense_path):
            n = storage.upload_directory(dense_path, f"{mission_s3_prefix}/dense/")
            upload_count += n
            report_mission_progress(vol_id, "UPLOADING", 96, log=f"Dense reconstruction uploaded ({n} files)")

        # 5. Gaussian splatting outputs (PLY models + checkpoints)
        if durable_checkpoint_dir and os.path.isdir(durable_checkpoint_dir):
            n = storage.upload_directory(
                durable_checkpoint_dir,
                f"{mission_s3_prefix}/gaussian/",
            )
            upload_count += n
            gaussian_upload_complete = True
            report_mission_progress(vol_id, "UPLOADING", 98, log=f"Gaussian models & checkpoints uploaded ({n} files)")

        report_mission_progress(
            vol_id, "UPLOADING", 99, log=f"All artifacts uploaded to S3 ({upload_count} files total)"
        )
    except Exception as upload_err:
        report_mission_progress(
            vol_id,
            "UPLOADING",
            98,
            log=(f"Warning: an optional recovery/debug artifact could not be uploaded: {upload_err}"),
        )

    return PipelinePublicationState(
        ortho_s3_key=ortho_s3_key,
        gaussian_upload_complete=gaussian_upload_complete,
    )


def cleanup_pipeline_workspace(
    workspace_dir: str,
    vol_id: str,
    *,
    final_pass: bool = False,
) -> None:
    if not os.path.isdir(workspace_dir):
        return
    try:
        shutil.rmtree(workspace_dir, ignore_errors=True)
        suffix = " (finally)" if final_pass else ""
        report_mission_progress(
            vol_id,
            "CLEANUP",
            99,
            log=f"Local workspace {workspace_dir} cleaned up{suffix}",
        )
    except Exception as cleanup_error:
        if not final_pass:
            report_mission_progress(
                vol_id,
                "CLEANUP",
                99,
                log=f"Warning: workspace cleanup failed: {cleanup_error}",
            )


def complete_colmap_pipeline(
    preparation: PipelinePreparation,
    publication_state: PipelinePublicationState,
    gaussian_state: PipelineGaussianState,
    workspace_dir: str,
    vol_id: str,
    mission_params: dict,
) -> None:
    facade_mode = preparation.facade_mode
    ortho_s3_key = publication_state.ortho_s3_key
    gaussian_upload_complete = publication_state.gaussian_upload_complete
    durable_checkpoint_dir = gaussian_state.durable_checkpoint_dir
    checkpoint_s3_prefix = gaussian_state.checkpoint_s3_prefix

    cleanup_pipeline_workspace(workspace_dir, vol_id)

    completion_details = None
    completion_log = "Pipeline complete!"
    if facade_mode:
        completion_details = {
            "event": "process_complete",
            "process": "facade",
            "terminal": True,
        }
        completion_log = (
            "Facade orthophoto published in a local coordinate frame; "
            "aerial detection stages were intentionally skipped."
        )
    report_mission_progress(
        vol_id,
        "DONE",
        100,
        status="success",
        log=completion_log,
        details=completion_details,
    )
    if not facade_mode:
        publish_next_stage_message(
            producer,
            TOPIC_OUT,
            vol_id,
            ortho_s3_key,
            mission_params,
            normalize_ai_backend,
        )
    if gaussian_upload_complete and durable_checkpoint_dir and os.path.isdir(durable_checkpoint_dir):
        try:
            storage.delete_prefix(checkpoint_s3_prefix + "/")
            shutil.rmtree(durable_checkpoint_dir, ignore_errors=True)
            report_mission_progress(
                vol_id,
                "CLEANUP",
                100,
                log=("Durable DroneGS recovery state retired after PLY/manifest/canary promotion."),
            )
        except Exception as retirement_error:
            report_mission_progress(
                vol_id,
                "CLEANUP",
                100,
                log=(f"Completed artifacts are promoted; recovery state was retained: {retirement_error}"),
            )


# This entry point coordinates independently testable, resumable stages.
def run_colmap_pipeline(
    workspace_dir: str,
    input_dataset: str,
    vol_id: str,
    mission_params: dict,
) -> None:
    try:
        preparation = prepare_colmap_pipeline_run(
            workspace_dir,
            input_dataset,
            vol_id,
            mission_params,
        )
        reconstruction = reconstruct_colmap_sparse(
            preparation,
            workspace_dir,
            vol_id,
        )
        rtk_state = refine_colmap_rtk(
            preparation,
            reconstruction,
            workspace_dir,
            vol_id,
        )
        # --- 7-8. Undistortion and geo-alignment ---
        alignment_state = undistort_and_align_colmap(
            preparation,
            reconstruction,
            rtk_state,
            workspace_dir,
            vol_id,
        )

        # --- 9. Gaussian Splatting product ---
        gaussian_state = run_gaussian_product(
            preparation,
            reconstruction,
            alignment_state,
            workspace_dir,
            vol_id,
        )
        # --- 10. Verified product publication ---
        publication_state = publish_colmap_products(
            preparation,
            reconstruction,
            rtk_state,
            alignment_state,
            gaussian_state,
            workspace_dir,
            vol_id,
        )
        # --- 11. Completion and recovery-state retirement ---
        complete_colmap_pipeline(
            preparation,
            publication_state,
            gaussian_state,
            workspace_dir,
            vol_id,
            mission_params,
        )

    except PipelineCancelledError as e:
        report_mission_progress(vol_id, "CANCELLED", 0, status="error", log=f"🚫 {str(e)}")
    except Exception as e:
        report_mission_progress(vol_id, "ERROR", 0, status="error", log=f"CRITICAL ERROR: {str(e)}")
        raise
    finally:
        # Always clean up local workspace to avoid filling the system disk.
        cleanup_pipeline_workspace(workspace_dir, vol_id, final_pass=True)
        # Release Python-owned memory after every mission. GPU resources are
        # intentionally left to the runtime and driver lifecycle.
        import gc

        gc.collect()


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
                raise ValueError(f"No input dataset specified for mission {mission_context.vol_id}")

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
