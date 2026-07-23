import os
import json
import shutil
import subprocess
import sys
import threading
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
from shared.pipeline_params import (
    normalize_feature_type,
    normalize_matcher_type,
)
from pipeline_support import (
    load_copy_manifest,
    detect_existing_pipeline,
    extract_gps_data,
    inspect_sparse_reconstruction,
    is_aliked_feature_type,
    merge_pipeline_params,
    normalize_ai_backend,
    read_saved_utm_crs,
    resolve_feature_family,
    resolve_feature_matching_type,
    sanitize_exif_for_colmap,
    save_utm_crs,
    save_copy_manifest,
    plan_clean_image_copy,
)
from runtime_support import run_command
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
        dense_path,
        os.path.join(workspace_dir, "sparse_geo"),
        os.path.join(workspace_dir, "alignment_transform.json"),
        os.path.join(workspace_dir, "orthomosaic.tif"),
        geo_data_file,
        f"{geo_data_file}.crs",
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
        
        images = [f for f in os.listdir(raw_image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        report_mission_progress(vol_id, "COPYING_IMAGES", 5, log=f"Copying {len(images)} images to clean workspace...")
        
        copied_count = 0
        skipped_count = 0
        copy_manifest = load_copy_manifest(clean_images_dir)
        for i, img in enumerate(images):
            try:
                cancellation_state.ensure_not_cancelled()
            except RuntimeError as error:
                raise PipelineCancelledError(str(error)) from error
            
            src_path = os.path.join(raw_image_dir, img)
            dst_path = os.path.join(clean_images_dir, img)
            needs_copy, source_descriptor = plan_clean_image_copy(src_path, dst_path, copy_manifest.get(img))

            if not needs_copy:
                skipped_count += 1
            else:
                shutil.copy2(src_path, dst_path)
                copied_count += 1
                if source_descriptor is None:
                    needs_copy, source_descriptor = plan_clean_image_copy(src_path, dst_path, copy_manifest.get(img))

            if source_descriptor is not None:
                copy_manifest[img] = source_descriptor

            if (i + 1) % 50 == 0 or i == len(images) - 1:
                save_copy_manifest(clean_images_dir, copy_manifest)
                
                report_mission_progress(
                    vol_id,
                    "COPYING_IMAGES",
                    5,
                    log=f"Processed {i + 1}/{len(images)} images (Copied: {copied_count}, Skipped: {skipped_count})",
                    details={
                        "event": "copy_progress",
                        "processed": i + 1,
                        "total": len(images),
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

        image_reader_camera_model = "OPENCV"
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
        
        # --- 2. GPS ---
        gps_done = os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0
        if gps_done:
            report_mission_progress(vol_id, "GPS_EXTRACTION", 12, log="Existing GPS data found, skipping extraction and inferring UTM CRS...")
            # We still need the UTM CRS for the ortho step
            utm_crs = read_saved_utm_crs(geo_data_file)
            images = [f for f in os.listdir(clean_images_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if utm_crs is None and images:
                try:
                    img_path = os.path.join(clean_images_dir, images[0])
                    with PILImage.open(img_path) as pil_img:
                        exif_data = pil_img._getexif()
                        if exif_data:
                            gps_ifd = exif_data.get(0x8825)
                            if gps_ifd:
                                gps_info = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                                lat_dms = gps_info.get("GPSLatitude")
                                lon_dms = gps_info.get("GPSLongitude")
                                if lat_dms and lon_dms:
                                    lat = float(lat_dms[0]) + float(lat_dms[1]) / 60 + float(lat_dms[2]) / 3600
                                    if gps_info.get("GPSLatitudeRef", "N") == "S":
                                        lat = -lat
                                    lon = float(lon_dms[0]) + float(lon_dms[1]) / 60 + float(lon_dms[2]) / 3600
                                    if gps_info.get("GPSLongitudeRef", "E") == "W":
                                        lon = -lon
                                    zone_number = int((lon + 180) / 6) + 1
                                    is_south = lat < 0
                                    utm_crs = f"EPSG:32{'7' if is_south else '6'}{zone_number:02d}"
                except Exception:
                    pass
            save_utm_crs(geo_data_file, utm_crs)
        else:
            utm_crs = extract_gps_data(clean_images_dir, geo_data_file, vol_id, report_mission_progress)
            save_utm_crs(geo_data_file, utm_crs)

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
            else:
                feat_cmd += [
                    "--FeatureExtraction.type", feature_type,
                    "--FeatureExtraction.use_gpu", "1",
                    "--FeatureExtraction.gpu_index", feature_gpu_index,
                    "--FeatureExtraction.max_image_size", params["feature_max_image_size"],
                    "--SiftExtraction.max_num_features", params["feature_max_num_features"],
                ]
            
            run_command(feat_cmd, vol_id, "FEATURES", 15, report_mission_progress, ensure_not_cancelled)
            
            # --- 4. SfM: Feature Matching ---
            match_cmd = [
                "colmap", "spatial_matcher",
                "--database_path", db_path,
                "--SpatialMatching.ignore_z", "1",
                "--FeatureMatching.type", resolved_matcher_type,
                "--FeatureMatching.use_gpu", "1",
                "--FeatureMatching.gpu_index", feature_gpu_index,
            ]
            
            run_command(match_cmd, vol_id, "MATCHING", 30, report_mission_progress, ensure_not_cancelled)
            
            # --- 5. SfM: View Graph Calibration (modern only) ---
            if params["use_view_graph_calibrator"]:
                report_mission_progress(vol_id, "CALIBRATING", 38, log="Running view graph calibration for GLOMAP...")
                run_command([
                    "colmap", "view_graph_calibrator",
                    "--database_path", db_path,
                ], vol_id, "CALIBRATING", 38, report_mission_progress, ensure_not_cancelled)
            
            # --- 6. SfM: Mapping ---
            os.makedirs(sparse_path, exist_ok=True)
            mapper_cmd = params["mapper_cmd"]
            # hierarchical_mapper was removed in COLMAP 4.x, fall back to mapper
            if mapper_cmd == "hierarchical_mapper":
                logger.warning("hierarchical_mapper not available in COLMAP 4.x, falling back to mapper")
                mapper_cmd = "mapper"
            map_cmd = [
                "colmap", mapper_cmd,
                "--database_path", db_path,
                "--image_path", clean_images_dir,
                "--output_path", sparse_path,
            ]
            if mapper_cmd == "global_mapper":
                map_cmd += [
                    "--GlobalMapper.ba_ceres_use_gpu", "1",
                    "--GlobalMapper.ba_ceres_gpu_index", ba_gpu_index,
                ]
            else:
                map_cmd += [
                    "--Mapper.ba_use_gpu", "1",
                    "--Mapper.ba_gpu_index", ba_gpu_index,
                ]
            
            try:
                run_command(map_cmd, vol_id, "MAPPING", 45, report_mission_progress, ensure_not_cancelled)
            except (RuntimeError, subprocess.CalledProcessError) as e:
                if "SIGABRT" in str(e) and "--Mapper.ba_use_gpu" in " ".join(map_cmd):
                    report_mission_progress(
                        vol_id, "MAPPING", 45,
                        log="GPU bundle adjustment crashed (likely OOM on large dataset). Retrying with CPU BA...",
                    )
                    shutil.rmtree(sparse_path, ignore_errors=True)
                    os.makedirs(sparse_path, exist_ok=True)
                    # Rebuild command without GPU BA
                    cpu_map_cmd = [
                        "colmap", mapper_cmd,
                        "--database_path", db_path,
                        "--image_path", clean_images_dir,
                        "--output_path", sparse_path,
                        "--Mapper.ba_use_gpu", "0",
                    ]
                    run_command(cpu_map_cmd, vol_id, "MAPPING", 45, report_mission_progress, ensure_not_cancelled)
                else:
                    raise

            sparse_model_path = os.path.join(sparse_path, "0")
            registered_images, sparse_points = inspect_sparse_reconstruction(sparse_model_path)
            if registered_images < 3 or sparse_points <= 0:
                report_mission_progress(
                    vol_id,
                    "MAPPING",
                    46,
                    log=(
                        f"Sparse reconstruction is too weak for MVS after {params['mapper_cmd']}: "
                        f"registered_images={registered_images}, points3D={sparse_points}."
                    ),
                )

                if params["mapper_cmd"] == "global_mapper":
                    report_mission_progress(
                        vol_id,
                        "MAPPING",
                        46,
                        log="Falling back to exhaustive_matcher + mapper because spatial/global mapping produced an unusable sparse model.",
                    )
                    shutil.rmtree(sparse_path, ignore_errors=True)
                    os.makedirs(sparse_path, exist_ok=True)

                    exhaustive_match_cmd = [
                        "colmap", "exhaustive_matcher",
                        "--database_path", db_path,
                        "--FeatureMatching.type", resolved_matcher_type,
                        "--FeatureMatching.use_gpu", "1",
                        "--FeatureMatching.gpu_index", feature_gpu_index,
                    ]
                    run_command(exhaustive_match_cmd, vol_id, "MATCHING", 33, report_mission_progress, ensure_not_cancelled)

                    fallback_map_cmd = [
                        "colmap", "mapper",
                        "--database_path", db_path,
                        "--image_path", clean_images_dir,
                        "--output_path", sparse_path,
                        "--Mapper.ba_use_gpu", "1",
                        "--Mapper.ba_gpu_index", ba_gpu_index,
                    ]
                    run_command(fallback_map_cmd, vol_id, "MAPPING", 47, report_mission_progress, ensure_not_cancelled)
                    registered_images, sparse_points = inspect_sparse_reconstruction(sparse_model_path)

                if registered_images < 3 or sparse_points <= 0:
                    raise RuntimeError(
                        "Sparse reconstruction is unusable for MVS "
                        f"(registered_images={registered_images}, points3D={sparse_points}). "
                        "The matcher/mapping stage did not recover enough overlap to build dense depth maps."
                    )
        else:
            report_mission_progress(vol_id, "MAPPING", 45, log="Sparse model found. Skipping SfM extraction and matching.")

        # --- 7. Undistort images for Gaussian Splatting ---
        # GS only needs the undistorted images + dense/sparse model.
        if ortho_only_ready:
            report_mission_progress(vol_id, "UNDISTORT", 75, log="Undistorted images found. Skipping undistortion.")
        else:
            if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
                run_command([
                    "colmap", "image_undistorter",
                    "--image_path", clean_images_dir,
                    "--input_path", os.path.join(sparse_path, "0"),
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
            if not (os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0):
                return None

            os.makedirs(sparse_geo_path, exist_ok=True)
            align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin"))
            if not align_done:
                run_command([
                    "colmap", "model_aligner",
                    "--input_path", os.path.join(sparse_path, "0"),
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
                    os.path.join(sparse_path, "0"),
                    sparse_geo_path,
                )
                transform_file = os.path.join(workspace_dir, "alignment_transform.json")
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

            # Auto data_factor: scale down training images based on dataset size and resolution
            gs_data_factor_raw = str(params.get("gs_data_factor", "auto"))
            if gs_data_factor_raw == "auto":
                images_dir = os.path.join(dense_path, "images")
                image_files = [f for f in os.listdir(images_dir)
                               if f.lower().endswith(('.jpg', '.jpeg', '.png'))] if os.path.isdir(images_dir) else []
                n_images_count = len(image_files)
                max_dim = 0
                if image_files:
                    try:
                        probe_path = os.path.join(images_dir, image_files[0])
                        with PILImage.open(probe_path) as img:
                            max_dim = max(img.size)
                    except Exception:
                        pass
                if max_dim > 4000 or n_images_count > 800:
                    gs_data_factor = 8
                elif max_dim > 2000 or n_images_count > 500:
                    gs_data_factor = 4
                elif max_dim > 1200:
                    gs_data_factor = 2
                else:
                    gs_data_factor = 1
                report_mission_progress(vol_id, "GAUSS", 95, log=f"Auto data_factor={gs_data_factor} for {n_images_count} images, max_dim={max_dim}px")
            else:
                gs_data_factor = int(gs_data_factor_raw)

            gs_iterations = int(params.get("gs_iterations", 30_000))
            gs_cap_max = int(params.get("gs_cap_max", 5_000_000))
            gs_sh_degree = int(params.get("gs_sh_degree", 3))
            gs_filter_enabled = params.get("gs_filter_enabled", True)
            gs_filter_max_scale = float(params.get("gs_filter_max_scale", 1.0))
            gs_filter_dist = float(params.get("gs_filter_dist", 1.0))
            gs_filter_opacity = float(params.get("gs_filter_opacity", 0.005))
            gs_filter_needle = float(params.get("gs_filter_needle", 0.0))
            gs_filter_sor = params.get("gs_filter_sor", False)
            gs_filter_sor_sigma = float(params.get("gs_filter_sor_sigma", 4.0))
            gs_filter_cc = params.get("gs_filter_cc", False)
            gs_filter_z_floater = params.get("gs_filter_z_floater", False)

            checkpoint_dir = os.path.join(workspace_dir, "gaussian_checkpoints")

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
                max_width=int(params.get("gs_max_width", 3840)),
                tile_mode=int(params.get("gs_tile_mode", 1)),
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
                checkpoint_dir=checkpoint_dir,
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
        #     geo_data.txt             — GPS from EXIF
        #     geo_data.txt.crs         — UTM CRS code
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

        report_mission_progress(vol_id, "UPLOADING", 90, log="Uploading ALL pipeline artifacts to S3...")
        upload_count = 0
        try:
            # 1. Orthomosaic
            ortho_s3_key = f"{mission_s3_prefix}/orthomosaic.tif"
            if os.path.exists(ortho_file):
                storage.upload_file(ortho_file, ortho_s3_key)
                upload_count += 1

            height_tif = os.path.join(workspace_dir, "orthomosaic.height.tif")
            if os.path.exists(height_tif):
                storage.upload_file(height_tif, f"{mission_s3_prefix}/orthomosaic.height.tif")
                upload_count += 1

            report_mission_progress(vol_id, "UPLOADING", 91, log="Orthomosaic uploaded")

            # 2. Alignment & geo data
            if align_tf and os.path.exists(align_tf):
                storage.upload_file(align_tf, f"{mission_s3_prefix}/alignment_transform.json")
                upload_count += 1
            if os.path.exists(geo_data_file):
                storage.upload_file(geo_data_file, f"{mission_s3_prefix}/geo_data.txt")
                upload_count += 1
                crs_file = f"{geo_data_file}.crs"
                if os.path.exists(crs_file):
                    storage.upload_file(crs_file, f"{mission_s3_prefix}/geo_data.txt.crs")
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
            checkpoint_dir = os.path.join(workspace_dir, "gaussian_checkpoints")
            if os.path.isdir(checkpoint_dir):
                n = storage.upload_directory(checkpoint_dir, f"{mission_s3_prefix}/gaussian/")
                upload_count += n
                report_mission_progress(vol_id, "UPLOADING", 98, log=f"Gaussian models & checkpoints uploaded ({n} files)")

            report_mission_progress(vol_id, "UPLOADING", 99, log=f"All artifacts uploaded to S3 ({upload_count} files total)")
        except Exception as upload_err:
            report_mission_progress(vol_id, "UPLOADING", 98, log=f"Warning: S3 upload partially failed: {upload_err}")
            ortho_s3_key = f"{mission_s3_prefix}/orthomosaic.tif"

        # --- Cleanup local workspace to free WSL disk ---
        try:
            shutil.rmtree(workspace_dir, ignore_errors=True)
            report_mission_progress(vol_id, "CLEANUP", 99, log=f"Local workspace {workspace_dir} cleaned up")
        except Exception as cleanup_err:
            report_mission_progress(vol_id, "CLEANUP", 99, log=f"Warning: workspace cleanup failed: {cleanup_err}")

        report_mission_progress(vol_id, "DONE", 100, status="success", log="Pipeline complete!")
        publish_next_stage_message(producer, TOPIC_OUT, vol_id, ortho_s3_key, mission_params, normalize_ai_backend)

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
