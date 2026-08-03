"""Sparse bootstrap, feature matching and reconstruction stage."""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import time

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
from pipeline_support import (
    extract_gps_data,
    inspect_sparse_quality,
    read_saved_projected_crs,
    read_saved_projected_crs_policy,
    sanitize_exif_for_colmap,
    save_projected_crs,
)
from runtime_support import run_command
from shared.json_io import atomic_write_json
from shared.rtk_refinement import inject_database_gravity_priors, load_rtk_records

from .. import runtime
from ..artifacts import dense_sparse_model_ready, invalidate_georeferencing_artifacts
from ..contracts import PipelinePreparation, PipelineReconstruction, SparseBootstrapState


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
    if gps_done and (
        not saved_projection_policy
        or saved_projection_policy.get("policy") != projected_crs_mode
    ):
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
        runtime.report_mission_progress(
            vol_id,
            "GPS_EXTRACTION",
            12,
            log="Facade mode uses no projected CRS and no absolute camera-position alignment.",
        )
    elif gps_done:
        utm_crs = saved_projected_crs
        runtime.report_mission_progress(
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
            runtime.report_mission_progress,
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
    sanitize_exif_for_colmap(clean_images_dir, vol_id, runtime.report_mission_progress)

    align_tf = os.path.join(workspace_dir, "alignment_transform.json")
    align_tf = align_tf if os.path.exists(align_tf) else None
    dense_sparse_ready = dense_sparse_model_ready(dense_path)

    # Gaussian Splatting only needs dense/sparse + undistorted images.
    gs_ready = dense_sparse_ready and os.path.isdir(os.path.join(dense_path, "images"))
    ortho_only_ready = gs_ready
    if ortho_only_ready:
        runtime.report_mission_progress(
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


def _build_feature_extraction_command(
    preparation: PipelinePreparation,
    vol_id: str,
) -> list[str]:
    params = preparation.params
    command = [
        "colmap",
        "feature_extractor",
        "--database_path",
        preparation.db_path,
        "--image_path",
        preparation.clean_images_dir,
        "--ImageReader.single_camera",
        "1",
        "--ImageReader.camera_model",
        preparation.image_reader_camera_model,
        "--FeatureExtraction.num_threads",
        str(params.get("feature_num_threads", "-1")),
    ]
    if preparation.image_reader_camera_params:
        command += [
            "--ImageReader.camera_params",
            preparation.image_reader_camera_params,
        ]

    if preparation.feature_family == "ALIKED":
        requested_max_size = int(float(params["feature_max_image_size"]))
        safe_max_size = int(float(os.getenv("ALIKED_SAFE_MAX_IMAGE_SIZE", "1600")))
        effective_max_size = min(requested_max_size, safe_max_size)
        if effective_max_size < requested_max_size:
            runtime.report_mission_progress(
                vol_id,
                "FEATURES",
                14,
                log=(
                    f"Clamping ALIKED extraction size from {requested_max_size}px to "
                    f"{effective_max_size}px to prevent ONNX CUDA memory blow-ups."
                ),
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
        command += [
            "--FeatureExtraction.type",
            params["feature_type"],
            "--FeatureExtraction.use_gpu",
            "1",
            "--FeatureExtraction.gpu_index",
            preparation.feature_gpu_index,
            "--FeatureExtraction.max_image_size",
            str(effective_max_size),
            "--AlikedExtraction.max_num_features",
            params["feature_max_num_features"],
            model_option,
            os.path.join(
                os.getenv("COLMAP_MODEL_DIR", "/usr/local/share/colmap/models"),
                model_filename,
            ),
        ]
    else:
        command += [
            "--FeatureExtraction.type",
            preparation.feature_type,
            "--FeatureExtraction.use_gpu",
            "1",
            "--FeatureExtraction.gpu_index",
            preparation.feature_gpu_index,
            "--FeatureExtraction.max_image_size",
            params["feature_max_image_size"],
            "--SiftExtraction.max_num_features",
            params["feature_max_num_features"],
            "--SiftExtraction.first_octave",
            str(params["sift_first_octave"]),
        ]
    return command


def _build_matching_command(
    preparation: PipelinePreparation,
    workspace_dir: str,
    vol_id: str,
    *,
    gps_done: bool,
) -> tuple[list[str], list[str], str]:
    params = preparation.params
    strategy = str(params.get("matching_strategy", "gps_pairs")).lower()
    model_dir = os.getenv("COLMAP_MODEL_DIR", "/usr/local/share/colmap/models")
    model_options: list[str] = []
    if preparation.resolved_matcher_type == "ALIKED_LIGHTGLUE":
        model_options = [
            "--AlikedMatching.lightglue_model_path",
            os.path.join(model_dir, "aliked-lightglue.onnx"),
        ]
    elif preparation.resolved_matcher_type == "SIFT_LIGHTGLUE":
        model_options = [
            "--SiftMatching.lightglue_model_path",
            os.path.join(model_dir, "sift-lightglue.onnx"),
        ]

    common_options = [
        "--database_path",
        preparation.db_path,
        "--FeatureMatching.type",
        preparation.resolved_matcher_type,
        "--FeatureMatching.use_gpu",
        "1",
        "--FeatureMatching.gpu_index",
        preparation.feature_gpu_index,
        "--FeatureMatching.guided_matching",
        "1" if params.get("guided_matching", False) else "0",
        "--FeatureMatching.max_num_matches",
        str(params["feature_max_num_matches"]),
    ]
    if strategy == "gps_pairs" and gps_done:
        positioned = parse_colmap_reference_file(preparation.geo_data_file)
        pairs, graph_stats = build_gps_pair_graph(
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
            graph_stats,
        )
        if pair_count == 0:
            raise RuntimeError(
                "GPS pair selection produced no pairs. Check EXIF positions "
                "or choose the spatial/sequential matching strategy."
            )
        runtime.report_mission_progress(
            vol_id,
            "MATCHING",
            25,
            log=(
                f"Matching {pair_count} bounded GPS/temporal pairs for "
                f"{graph_stats['positioned_images']} positioned images "
                f"(mean degree {graph_stats['mean_degree']:.1f})."
            ),
            details={"event": "pair_graph", **graph_stats},
        )
        command = [
            "colmap",
            "matches_importer",
            "--database_path",
            preparation.db_path,
            "--match_list_path",
            pair_list_path,
            "--match_type",
            "pairs",
            *common_options[2:],
        ]
    elif strategy == "sequential":
        command = ["colmap", "sequential_matcher", *common_options]
    else:
        if strategy == "gps_pairs":
            runtime.report_mission_progress(
                vol_id,
                "MATCHING",
                25,
                log="GPS pair selection unavailable; using bounded COLMAP spatial matching.",
            )
        command = [
            "colmap",
            "spatial_matcher",
            "--database_path",
            preparation.db_path,
            "--SpatialMatching.ignore_z",
            "1",
            "--SpatialMatching.max_num_neighbors",
            str(params["gps_pair_max_neighbors"]),
            "--SpatialMatching.min_num_neighbors",
            str(params["gps_pair_min_neighbors"]),
            *common_options[2:],
        ]
    return command + model_options, model_options, strategy


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
    resolved_matcher_type = preparation.resolved_matcher_type
    feature_gpu_index = preparation.feature_gpu_index
    ba_gpu_index = preparation.ba_gpu_index
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
        feat_cmd = _build_feature_extraction_command(preparation, vol_id)
        run_command(feat_cmd, vol_id, "FEATURES", 15, runtime.report_mission_progress, runtime.ensure_not_cancelled)

        # --- 4. SfM: bounded Feature Matching ---
        match_cmd, matching_model_options, matching_strategy = _build_matching_command(
            preparation,
            workspace_dir,
            vol_id,
            gps_done=gps_done,
        )
        run_command(
            match_cmd,
            vol_id,
            "MATCHING",
            30,
            runtime.report_mission_progress,
            runtime.ensure_not_cancelled,
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
                runtime.report_mission_progress,
                runtime.ensure_not_cancelled,
            )
        match_counts = database_counts(db_path)
        runtime.report_mission_progress(
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
            runtime.report_mission_progress(
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
            runtime.report_mission_progress(vol_id, "CALIBRATING", 38, log="Running view graph calibration for GLOMAP...")
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
                runtime.report_mission_progress,
                runtime.ensure_not_cancelled,
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
            math.ceil(len(images) * minimum_registration_ratio),
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
            runtime.report_mission_progress(
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
                runtime.report_mission_progress,
                runtime.ensure_not_cancelled,
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
            runtime.report_mission_progress(
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
        runtime.report_mission_progress(
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
            runtime.report_mission_progress(
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
        runtime.report_mission_progress(vol_id, "MAPPING", 45, log="Sparse model found. Skipping SfM extraction and matching.")

    return PipelineReconstruction(
        utm_crs=utm_crs,
        alignment_transform_path=align_tf,
        ortho_only_ready=ortho_only_ready,
    )
