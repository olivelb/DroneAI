"""DroneGS training, qualification and Gaussian product stage."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image as PILImage

from pipeline_support import choose_dronegs_data_factor
from shared import storage
from shared.dronegs_profile import DRONEGS_PRODUCTION_PROFILE_V1
from gaussian_ortho.generate_gaussian_orthophoto import GaussianOrthoConfig

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


@dataclass(frozen=True)
class GaussianProductRun:
    """Resolved runtime inputs shared by bounded Gaussian stage Jobs."""

    config: GaussianOrthoConfig
    trainer_backend: str
    checkpoint_s3_prefix: str


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

    max_training_width = int(params.get("gs_max_width", DRONEGS_PRODUCTION_PROFILE_V1.max_width))
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


def _training_workspace_root(vol_id: str) -> str | None:
    """Resolve optional reconstructible training storage outside checkpoints."""

    root = os.getenv("DRONEAI_GAUSSIAN_TRAINING_WORKSPACE_ROOT")
    if not root:
        return None
    workspace_root = os.path.join(root, vol_id)
    os.makedirs(workspace_root, exist_ok=True)
    return workspace_root


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


def prepare_gaussian_product_run(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    alignment_state: PipelineAlignmentState,
    workspace_dir: str,
    vol_id: str,
    *,
    prepare_checkpoints: bool = True,
) -> GaussianProductRun:
    """Resolve one immutable Gaussian recipe from portable COLMAP state."""
    params = preparation.params
    facade_mode = preparation.facade_mode
    dense_path = preparation.dense_path
    ortho_file = os.path.join(
        workspace_dir,
        "facade_orthophoto.tif" if facade_mode else "orthomosaic.tif",
    )
    align_tf = alignment_state.alignment_transform_path
    workspace_transform = os.path.join(workspace_dir, "alignment_transform.json")
    if not facade_mode and os.path.exists(workspace_transform):
        align_tf = workspace_transform
    if not dense_sparse_model_ready(dense_path):
        raise RuntimeError("Gaussian Splatting requires dense/sparse model (cameras.bin, images.bin, points3D.bin).")

    data_factor = _resolve_data_factor(params, dense_path, vol_id)
    resolved, warnings = resolve_dronegs_config(
        params,
        facade_mode=facade_mode,
        data_factor=data_factor,
    )
    _report_config_warnings(vol_id, warnings)
    checkpoint_s3_prefix = f"{preparation.mission_s3_prefix}/gaussian-checkpoints"
    if prepare_checkpoints:
        checkpoint_dir, checkpoint_s3_prefix = _prepare_checkpoint_store(
            workspace_dir,
            preparation.mission_s3_prefix,
            vol_id,
        )
        checkpoint_callback = _checkpoint_callback(
            checkpoint_dir,
            checkpoint_s3_prefix,
            vol_id,
        )
    else:
        checkpoint_dir = os.path.join(workspace_dir, ".droneai", "checkpoints")
        checkpoint_callback = None
    config = GaussianOrthoConfig(
        dense_path=dense_path,
        ortho_file=ortho_file,
        utm_crs=reconstruction.utm_crs,
        vol_id=vol_id,
        transform_file=align_tf,
        report_fn=runtime.report_mission_progress,
        resolution=resolved.resolution,
        iterations=resolved.iterations,
        partition_m=1,
        partition_n=1,
        training_workspace_root=_training_workspace_root(vol_id),
        partition_overlap=0.20,
        resident_partitioning=resolved.resident_partitioning,
        sh_degree=resolved.sh_degree,
        opacity_sh_enabled=True,
        checkpoint_dir=checkpoint_dir,
        data_factor=resolved.data_factor,
        max_width=resolved.max_width,
        ortho_mip_filter_variance=resolved.mip_filter_variance,
        ortho_mip_filter_compensation=resolved.mip_filter_compensation,
        tile_mode=resolved.tile_mode,
        tile_mode_auto=resolved.tile_mode_auto,
        cap_max=resolved.cap_max,
        capacity_mode=resolved.capacity_mode,
        capacity_floor=resolved.capacity_floor,
        target_gaussian_spacing_pixels=(resolved.target_gaussian_spacing_pixels),
        filter_enabled=resolved.filter_enabled,
        filter_max_scale=resolved.filter_max_scale,
        filter_min_retained_ratio=resolved.filter_min_retained_ratio,
        filter_dist_multiplier=resolved.filter_dist,
        filter_opacity_threshold=resolved.filter_opacity,
        filter_needle_ratio=resolved.filter_needle,
        filter_sor=resolved.filter_sor,
        filter_sor_sigma=resolved.filter_sor_sigma,
        filter_cc=resolved.filter_cc,
        filter_z_floater=resolved.filter_z_floater,
        coverage_gate_enabled=resolved.coverage_gate_enabled,
        coverage_grid_size=resolved.coverage_grid_size,
        coverage_min_valid_ratio=resolved.coverage_min_valid_ratio,
        coverage_cell_threshold=resolved.coverage_cell_threshold,
        coverage_min_covered_cells_ratio=(resolved.coverage_min_covered_cells_ratio),
        coverage_min_worst_cell_ratio=resolved.coverage_min_worst_cell_ratio,
        coverage_min_camera_cell_ratio=resolved.coverage_min_camera_cell_ratio,
        verbose=False,
        training_seed=resolved.seed,
        dronegs_profile_id=resolved.profile_id,
        dronegs_qualification_policy_id=resolved.qualification_policy_id,
        dronegs_optimizer_profile=resolved.optimizer_profile,
        dronegs_pruning_policy=resolved.pruning_policy,
        dronegs_raster_profile=resolved.raster_profile,
        dronegs_initial_scale_policy=resolved.initial_scale_policy,
        dronegs_initial_max_projected_sigma_pixels=(resolved.initial_max_projected_sigma_pixels),
        dronegs_maximum_scale_growth_factor=(resolved.maximum_scale_growth_factor),
        dronegs_capacity_targeted_growth=resolved.capacity_targeted_growth,
        dronegs_sh_degree_interval=resolved.sh_degree_interval,
        dronegs_topology_cooldown=resolved.topology_cooldown,
        dronegs_photometric_finish=resolved.photometric_finish,
        dronegs_photometric_mse_percent=resolved.photometric_mse_percent,
        dronegs_checkpoint_every=resolved.checkpoint_every,
        dronegs_test_every=resolved.test_every,
        dronegs_test_split=resolved.test_split,
        dronegs_test_guard_percent=resolved.test_guard_percent,
        dronegs_canary_min_psnr=resolved.canary_min_psnr,
        dronegs_canary_min_ssim=resolved.canary_min_ssim,
        cancellation_check=runtime.ensure_not_cancelled,
        checkpoint_callback=checkpoint_callback,
        render_mode=preparation.orthophoto_mode,
        facade_scale_mode=str(params["facade_scale_mode"]),
        facade_meters_per_model_unit=float(params["facade_meters_per_model_unit"]),
        facade_frame_report=os.path.join(workspace_dir, "facade_frame.json"),
        facade_texture_max_incidence_deg=float(params["facade_texture_max_incidence_deg"]),
        facade_depth_iqr_multiplier=float(params["facade_depth_iqr_multiplier"]),
        facade_depth_rear_iqr_multiplier=float(params["facade_depth_rear_iqr_multiplier"]),
        facade_seed_max_reprojection_error=float(params["facade_seed_max_reprojection_error"]),
        facade_seed_min_track_length=int(params["facade_seed_min_track_length"]),
    )
    return GaussianProductRun(
        config=config,
        trainer_backend=resolved.backend,
        checkpoint_s3_prefix=checkpoint_s3_prefix,
    )


def run_gaussian_product(
    preparation: PipelinePreparation,
    reconstruction: PipelineReconstruction,
    alignment_state: PipelineAlignmentState,
    workspace_dir: str,
    vol_id: str,
) -> PipelineGaussianState:
    facade_mode = preparation.facade_mode
    try:
        import gc
        import traceback as _tb

        app1_dir = str(APP1_DIR)
        if app1_dir not in sys.path:
            sys.path.insert(0, app1_dir)
        from gaussian_ortho.generate_gaussian_orthophoto import (
            generate_gaussian_orthophoto,
        )

        gc.collect()
        try:
            import cupy as _cp

            _cp.get_default_memory_pool().free_all_blocks()
            _cp.get_default_pinned_memory_pool().free_all_blocks()
        except Exception:
            pass

        product_run = prepare_gaussian_product_run(
            preparation,
            reconstruction,
            alignment_state,
            workspace_dir,
            vol_id,
        )
        config = product_run.config

        result = generate_gaussian_orthophoto(
            dense_path=config.dense_path,
            ortho_file=config.ortho_file,
            utm_crs=config.utm_crs,
            vol_id=config.vol_id,
            transform_file=config.transform_file,
            report_fn=config.report_fn,
            resolution=config.resolution,
            iterations=config.iterations,
            partition_m=config.partition_m,
            partition_n=config.partition_n,
            partition_overlap=config.partition_overlap,
            sh_degree=config.sh_degree,
            opacity_sh_enabled=config.opacity_sh_enabled,
            checkpoint_dir=config.checkpoint_dir,
            training_workspace_root=config.training_workspace_root,
            data_factor=config.data_factor,
            max_width=config.max_width,
            ortho_mip_filter_variance=config.ortho_mip_filter_variance,
            ortho_mip_filter_compensation=config.ortho_mip_filter_compensation,
            tile_mode=config.tile_mode,
            tile_mode_auto=config.tile_mode_auto,
            cap_max=config.cap_max,
            capacity_mode=config.capacity_mode,
            capacity_floor=config.capacity_floor,
            target_gaussian_spacing_pixels=(config.target_gaussian_spacing_pixels),
            filter_enabled=config.filter_enabled,
            filter_max_scale=config.filter_max_scale,
            filter_min_retained_ratio=config.filter_min_retained_ratio,
            filter_dist_multiplier=config.filter_dist_multiplier,
            filter_opacity_threshold=config.filter_opacity_threshold,
            filter_needle_ratio=config.filter_needle_ratio,
            filter_sor=config.filter_sor,
            filter_sor_sigma=config.filter_sor_sigma,
            filter_cc=config.filter_cc,
            filter_z_floater=config.filter_z_floater,
            coverage_gate_enabled=config.coverage_gate_enabled,
            coverage_grid_size=config.coverage_grid_size,
            coverage_min_valid_ratio=config.coverage_min_valid_ratio,
            coverage_cell_threshold=config.coverage_cell_threshold,
            coverage_min_covered_cells_ratio=(config.coverage_min_covered_cells_ratio),
            coverage_min_worst_cell_ratio=(config.coverage_min_worst_cell_ratio),
            coverage_min_camera_cell_ratio=(config.coverage_min_camera_cell_ratio),
            verbose=config.verbose,
            trainer_backend=product_run.trainer_backend,
            training_seed=config.training_seed,
            dronegs_profile_id=config.dronegs_profile_id,
            dronegs_qualification_policy_id=config.dronegs_qualification_policy_id,
            dronegs_optimizer_profile=config.dronegs_optimizer_profile,
            dronegs_pruning_policy=config.dronegs_pruning_policy,
            dronegs_raster_profile=config.dronegs_raster_profile,
            dronegs_initial_scale_policy=config.dronegs_initial_scale_policy,
            dronegs_initial_max_projected_sigma_pixels=(config.dronegs_initial_max_projected_sigma_pixels),
            dronegs_maximum_scale_growth_factor=(config.dronegs_maximum_scale_growth_factor),
            dronegs_capacity_targeted_growth=(config.dronegs_capacity_targeted_growth),
            dronegs_sh_degree_interval=config.dronegs_sh_degree_interval,
            dronegs_topology_cooldown=config.dronegs_topology_cooldown,
            dronegs_photometric_finish=config.dronegs_photometric_finish,
            dronegs_photometric_mse_percent=config.dronegs_photometric_mse_percent,
            dronegs_checkpoint_every=config.dronegs_checkpoint_every,
            dronegs_test_every=config.dronegs_test_every,
            dronegs_test_split=config.dronegs_test_split,
            dronegs_test_guard_percent=config.dronegs_test_guard_percent,
            dronegs_canary_min_psnr=config.dronegs_canary_min_psnr,
            dronegs_canary_min_ssim=config.dronegs_canary_min_ssim,
            cancellation_check=config.cancellation_check,
            checkpoint_callback=config.checkpoint_callback,
            render_mode=config.render_mode,
            facade_scale_mode=config.facade_scale_mode,
            facade_meters_per_model_unit=config.facade_meters_per_model_unit,
            facade_frame_report=config.facade_frame_report,
            facade_texture_max_incidence_deg=(config.facade_texture_max_incidence_deg),
            facade_depth_iqr_multiplier=config.facade_depth_iqr_multiplier,
            facade_depth_rear_iqr_multiplier=(config.facade_depth_rear_iqr_multiplier),
            facade_seed_max_reprojection_error=(config.facade_seed_max_reprojection_error),
            facade_seed_min_track_length=config.facade_seed_min_track_length,
        )
        runtime.report_mission_progress(
            vol_id,
            "GAUSS",
            100,
            log=f"Gaussian Splatting {'facade orthophoto' if facade_mode else 'orthomosaic'} complete: "
            f"{result['width']}x{result['height']}px, "
            f"{result['n_gaussians']} Gaussians, "
            f"pixel size={config.resolution} {result['gsd_units']}",
        )
    except Exception as e:
        _tb.print_exc()
        runtime.report_mission_progress(vol_id, "ORTHO", 95, log=f"Gaussian Splatting ortho failed: {e}")
        raise

    return PipelineGaussianState(
        ortho_file=config.ortho_file,
        result=result,
        durable_checkpoint_dir=config.checkpoint_dir,
        checkpoint_s3_prefix=product_run.checkpoint_s3_prefix,
        profile_id=config.dronegs_profile_id,
        qualification_policy_id=config.dronegs_qualification_policy_id,
    )
