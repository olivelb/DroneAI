"""DroneGS training, qualification and Gaussian product stage."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from pipeline_support import choose_dronegs_data_factor
from shared import storage
from shared.dronegs_profile import DRONEGS_PRODUCTION_PROFILE_V1

from .. import runtime
from ..artifacts import dense_sparse_model_ready
from ..contracts import (
    PipelineAlignmentState,
    PipelineGaussianState,
    PipelinePreparation,
    PipelineReconstruction,
)
from ..dronegs_config import resolve_dronegs_config

APP1_DIR = Path(__file__).resolve().parents[2]


def _resolve_data_factor(params: dict[str, Any], dense_path: str, vol_id: str) -> int:
    """Choose source downscaling without blurring below the training ceiling."""
    raw_value = str(params.get("gs_data_factor", "auto"))
    if raw_value != "auto":
        return int(raw_value)

    images_dir = os.path.join(dense_path, "images")
    image_files = (
        [name for name in os.listdir(images_dir) if name.lower().endswith((".jpg", ".jpeg", ".png"))]
        if os.path.isdir(images_dir)
        else []
    )
    max_dimension = 0
    if image_files:
        try:
            with PILImage.open(os.path.join(images_dir, image_files[0])) as image:
                max_dimension = max(image.size)
        except Exception:
            pass

    max_training_width = int(
        params.get("gs_max_width", DRONEGS_PRODUCTION_PROFILE_V1.max_width)
    )
    data_factor = choose_dronegs_data_factor(max_dimension, max_training_width)
    runtime.report_mission_progress(
        vol_id,
        "GAUSS",
        95,
        log=(
            f"Auto data_factor={data_factor} preserves the configured "
            f"{max_training_width}px training ceiling from a {max_dimension}px source."
        ),
    )
    return int(data_factor)


def _prepare_checkpoint_store(
    workspace_dir: str,
    mission_s3_prefix: str,
    vol_id: str,
) -> tuple[str, str]:
    checkpoint_root = os.getenv("DRONEGS_CHECKPOINT_ROOT") or os.path.join(
        os.path.dirname(workspace_dir),
        ".dronegs-checkpoints",
    )
    durable_checkpoint_dir = os.path.join(checkpoint_root, vol_id)
    os.makedirs(durable_checkpoint_dir, exist_ok=True)
    checkpoint_s3_prefix = f"{mission_s3_prefix}/gaussian-checkpoints"
    if any(path.is_file() for path in Path(durable_checkpoint_dir).rglob("*")):
        return durable_checkpoint_dir, checkpoint_s3_prefix

    try:
        restored_count = storage.download_directory(
            checkpoint_s3_prefix + "/",
            durable_checkpoint_dir,
        )
        if restored_count:
            runtime.report_mission_progress(
                vol_id,
                "GAUSS",
                94,
                log=f"Restored {restored_count} durable DroneGS artifacts from S3.",
            )
    except Exception as restore_error:
        runtime.report_mission_progress(
            vol_id,
            "GAUSS",
            94,
            log=f"No remote DroneGS recovery state restored: {restore_error}",
        )
    return durable_checkpoint_dir, checkpoint_s3_prefix


def _checkpoint_callback(
    durable_checkpoint_dir: str,
    checkpoint_s3_prefix: str,
    vol_id: str,
) -> Callable[[Path, int], None]:
    checkpoint_root = Path(durable_checkpoint_dir).resolve()

    def persist(checkpoint_path: Path, iteration: int) -> None:
        relative = checkpoint_path.resolve().relative_to(checkpoint_root)
        s3_key = f"{checkpoint_s3_prefix}/{relative.as_posix()}"
        try:
            storage.upload_file(checkpoint_path, s3_key)
            runtime.report_mission_progress(
                vol_id,
                "GAUSS",
                95,
                log=f"Durable DroneGS checkpoint synced at iteration {iteration}.",
            )
        except Exception as sync_error:
            runtime.report_mission_progress(
                vol_id,
                "GAUSS",
                95,
                log=f"DroneGS checkpoint remains locally durable; S3 sync failed: {sync_error}",
            )

    return persist


def _report_config_warnings(vol_id: str, warnings: tuple[str, ...]) -> None:
    for warning in warnings:
        runtime.report_mission_progress(vol_id, "GAUSS", 94, log=warning)


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

        app1_dir = str(APP1_DIR)
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

        # Dataset count is handled by tile mode and Gaussian caps, not by
        # uniformly blurring every image.
        gs_data_factor = _resolve_data_factor(params, dense_path, vol_id)
        gs_config, config_warnings = resolve_dronegs_config(
            params,
            facade_mode=facade_mode,
            data_factor=gs_data_factor,
        )
        _report_config_warnings(vol_id, config_warnings)

        durable_checkpoint_dir, checkpoint_s3_prefix = _prepare_checkpoint_store(
            workspace_dir,
            mission_s3_prefix,
            vol_id,
        )
        persist_dronegs_checkpoint = _checkpoint_callback(
            durable_checkpoint_dir,
            checkpoint_s3_prefix,
            vol_id,
        )

        result = generate_gaussian_orthophoto(
            dense_path=dense_path,
            ortho_file=ortho_file,
            utm_crs=utm_crs,
            vol_id=vol_id,
            transform_file=align_tf,
            report_fn=runtime.report_mission_progress,
            resolution=gs_config.resolution,
            iterations=gs_config.iterations,
            sh_degree=gs_config.sh_degree,
            data_factor=gs_config.data_factor,
            max_width=gs_config.max_width,
            ortho_mip_filter_variance=gs_config.mip_filter_variance,
            ortho_mip_filter_compensation=gs_config.mip_filter_compensation,
            tile_mode=gs_config.tile_mode,
            cap_max=gs_config.cap_max,
            filter_enabled=gs_config.filter_enabled,
            filter_max_scale=gs_config.filter_max_scale,
            filter_min_retained_ratio=gs_config.filter_min_retained_ratio,
            filter_dist_multiplier=gs_config.filter_dist,
            filter_opacity_threshold=gs_config.filter_opacity,
            filter_needle_ratio=gs_config.filter_needle,
            filter_sor=gs_config.filter_sor,
            filter_sor_sigma=gs_config.filter_sor_sigma,
            filter_cc=gs_config.filter_cc,
            filter_z_floater=gs_config.filter_z_floater,
            coverage_gate_enabled=gs_config.coverage_gate_enabled,
            coverage_grid_size=gs_config.coverage_grid_size,
            coverage_min_valid_ratio=gs_config.coverage_min_valid_ratio,
            coverage_cell_threshold=gs_config.coverage_cell_threshold,
            coverage_min_covered_cells_ratio=(
                gs_config.coverage_min_covered_cells_ratio
            ),
            coverage_min_worst_cell_ratio=(
                gs_config.coverage_min_worst_cell_ratio
            ),
            coverage_min_camera_cell_ratio=(
                gs_config.coverage_min_camera_cell_ratio
            ),
            checkpoint_dir=durable_checkpoint_dir,
            trainer_backend=gs_config.backend,
            training_seed=gs_config.seed,
            dronegs_profile_id=gs_config.profile_id,
            dronegs_qualification_policy_id=gs_config.qualification_policy_id,
            dronegs_optimizer_profile=gs_config.optimizer_profile,
            dronegs_pruning_policy=gs_config.pruning_policy,
            dronegs_raster_profile=gs_config.raster_profile,
            dronegs_sh_degree_interval=gs_config.sh_degree_interval,
            dronegs_topology_cooldown=gs_config.topology_cooldown,
            dronegs_photometric_finish=gs_config.photometric_finish,
            dronegs_photometric_mse_percent=gs_config.photometric_mse_percent,
            dronegs_checkpoint_every=gs_config.checkpoint_every,
            dronegs_test_every=gs_config.test_every,
            dronegs_test_split=gs_config.test_split,
            dronegs_test_guard_percent=gs_config.test_guard_percent,
            dronegs_canary_min_psnr=gs_config.canary_min_psnr,
            dronegs_canary_min_ssim=gs_config.canary_min_ssim,
            cancellation_check=runtime.ensure_not_cancelled,
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
        runtime.report_mission_progress(
            vol_id,
            "GAUSS",
            100,
            log=f"Gaussian Splatting {'facade orthophoto' if facade_mode else 'orthomosaic'} complete: "
            f"{result['width']}x{result['height']}px, "
            f"{result['n_gaussians']} Gaussians, "
            f"pixel size={gs_config.resolution} {result['gsd_units']}",
        )
    except Exception as e:
        _tb.print_exc()
        runtime.report_mission_progress(vol_id, "ORTHO", 95, log=f"Gaussian Splatting ortho failed: {e}")
        raise

    return PipelineGaussianState(
        ortho_file=ortho_file,
        result=result,
        durable_checkpoint_dir=durable_checkpoint_dir,
        checkpoint_s3_prefix=checkpoint_s3_prefix,
        profile_id=gs_config.profile_id,
        qualification_policy_id=gs_config.qualification_policy_id,
    )
