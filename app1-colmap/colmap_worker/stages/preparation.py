"""Input preparation stage for the COLMAP worker."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from shared import storage
from shared.facade_process import apply_facade_process_profile
from shared.facade_selection import (
    deduplicate_identical_basenames,
    exclude_basename_ranges,
    select_facade_images,
)
from shared.gcp_control import prepare_gcp_assets
from shared.json_io import atomic_write_json
from shared.pipeline_params import normalize_feature_type, normalize_matcher_type
from pipeline_support import (
    build_colmap_cache_config,
    changed_colmap_cache_parameters,
    detect_existing_pipeline,
    discover_input_assets,
    load_colmap_cache_config,
    load_copy_manifest,
    merge_pipeline_params,
    plan_clean_image_copy,
    resolve_feature_family,
    resolve_feature_matching_type,
    save_colmap_cache_config,
    save_copy_manifest,
)

from .. import runtime
from ..artifacts import invalidate_pipeline_artifacts, normalize_gpu_index
from ..contracts import PipelinePreparation


def prepare_colmap_pipeline_run(
    workspace_dir: str,
    input_dataset: str,
    vol_id: str,
    mission_params: dict,
) -> PipelinePreparation:
    # --- Pipeline selection ---
    pipeline_mode = mission_params.get("pipeline", "modern")
    if pipeline_mode not in ("modern", "legacy"):
        runtime.report_mission_progress(vol_id, "WARNING", 1, log=f"Unknown pipeline '{pipeline_mode}', defaulting to 'modern'")
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
    runtime.report_mission_progress(
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
    runtime.report_mission_progress(vol_id, "PREPARING", 2, log=f"Creating workspace at {workspace_dir}")
    os.makedirs(workspace_dir, exist_ok=True)

    raw_image_dir = os.path.join(workspace_dir, "raw_images")
    os.makedirs(raw_image_dir, exist_ok=True)
    clean_images_dir = os.path.join(workspace_dir, "clean_images")
    os.makedirs(clean_images_dir, exist_ok=True)

    # Download input images from S3
    # Normalize prefix: strip trailing slashes to avoid double-slash S3 keys
    input_dataset = input_dataset.rstrip("/")
    runtime.report_mission_progress(
        vol_id, "DOWNLOADING_IMAGES", 3, log=f"Downloading input images from S3 prefix: {input_dataset}"
    )
    try:
        n_downloaded = storage.download_directory(input_dataset + "/", raw_image_dir)
        if n_downloaded == 0:
            # Try without trailing slash
            n_downloaded = storage.download_directory(input_dataset, raw_image_dir)
        runtime.report_mission_progress(vol_id, "DOWNLOADING_IMAGES", 5, log=f"Downloaded {n_downloaded} files from S3")
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
        runtime.report_mission_progress(
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
        runtime.report_mission_progress(
            vol_id,
            "PREPARING",
            5,
            log=(f"Facade selection retained {len(images)}/{len(raw_images)} images with mode={selection_mode}."),
            details={"event": "facade_selection", **selection_report},
        )
    else:
        images, position_sidecars = discover_input_assets(raw_image_dir)
    copy_candidates = images + position_sidecars
    runtime.report_mission_progress(
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
            runtime.cancellation_state.ensure_not_cancelled()
        except RuntimeError as error:
            raise runtime.PipelineCancelledError(str(error)) from error

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

            runtime.report_mission_progress(
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
        runtime.report_mission_progress(vol_id, "COPYING_IMAGES", 5, log="Removed raw_images to free disk space")

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
            runtime.report_mission_progress(
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
