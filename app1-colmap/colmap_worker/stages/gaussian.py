"""DroneGS training, qualification and Gaussian product stage."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image as PILImage

from pipeline_support import choose_dronegs_data_factor
from shared import storage
from shared.dronegs_profile import DRONEGS_PRODUCTION_PROFILE_V1, DRONEGS_QUALIFICATION_POLICY_ID
from shared.facade_process import (
    FACADE_DRONEGS_IDENTITY_PARAMETERS,
    FACADE_DRONEGS_PROFILE_ID,
    FACADE_QUALIFICATION_POLICY_ID,
    FACADE_QUALIFICATION_THRESHOLDS,
)

from .. import runtime
from ..artifacts import dense_sparse_model_ready
from ..contracts import (
    PipelineAlignmentState,
    PipelineGaussianState,
    PipelinePreparation,
    PipelineReconstruction,
)

APP1_DIR = Path(__file__).resolve().parents[2]


def _resolve_data_factor(params: dict, dense_path: str, vol_id: str) -> int:
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
    return data_factor


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

        ortho_resolution = float(params.get("ortho_mesh_resolution", 0.02))

        # Dataset count is handled by tile mode and Gaussian caps, not by
        # uniformly blurring every image.
        gs_data_factor = _resolve_data_factor(params, dense_path, vol_id)

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
                runtime.report_mission_progress(
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
            runtime.report_mission_progress(
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
                    runtime.report_mission_progress(
                        vol_id,
                        "GAUSS",
                        94,
                        log=(f"Restored {restored_count} durable DroneGS artifacts from S3."),
                    )
            except Exception as restore_error:
                runtime.report_mission_progress(
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
                runtime.report_mission_progress(
                    vol_id,
                    "GAUSS",
                    95,
                    log=(f"Durable DroneGS checkpoint synced at iteration {iteration}."),
                )
            except Exception as sync_error:
                runtime.report_mission_progress(
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
            report_fn=runtime.report_mission_progress,
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
            f"pixel size={ortho_resolution} {result['gsd_units']}",
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
        profile_id=gs_profile_id,
        qualification_policy_id=gs_qualification_policy_id,
    )
