"""
Main entry point for Gaussian Splatting orthophoto generation.

Provides `generate_gaussian_orthophoto()` with the same interface pattern
as `generate_true_orthophoto_pytorch()` from ortho_dsm.py, making it easy
to swap in from the existing pipeline.

Pipeline:
  1. Load COLMAP reconstruction + alignment transform
  2. Partition scene (VastGaussian, if m×n > 1×1)
  3. Train Gaussian model per cell via the selected headless backend
  4. Merge cell models
  5. Render orthographic TDOM (custom CUDA rasterisation via CuPy)
  6. Write GeoTIFF
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from shared.dronegs_profile import (
    DRONEGS_PRODUCTION_PROFILE_V1,
    effective_raster_profile,
)

from .colmap_loader import (
    load_colmap_reconstruction,
    apply_sim3_to_points,
)
from .scene_info import build_scene_info
from .colmap_subset import (
    export_colmap_subset,
)
from gaussian_training import (
    DroneGSTuning,
    TrainingRequest,
    TrainingResult,
    resolve_training_backend,
)
from gaussian_training.dataset_identity import compute_dataset_identity
from gaussian_training.manifest_contract import (
    load_run_manifest,
    manifest_matches_ply,
    validate_run_manifest,
)
from .partition import partition_scene
from .geo_writer import write_geotiff
from .exif_altitude import extract_exif_altitudes, compute_colmap_scale


def _report(vol_id, step, progress, msg, report_fn):
    if report_fn:
        report_fn(vol_id, step, progress, log=msg)
    else:
        print(f"[{step} {progress}%] {msg}")


def _reusable_dronegs_result(
    request: TrainingRequest,
    *,
    trainer_binary_sha256: str,
) -> TrainingResult | None:
    """Return a previously promoted result only when its contract matches."""
    output = Path(request.output_path)
    manifest_path = output / "trainer_run.json"
    canary_path = output / "canary_result.json"
    ply_path = output / "point_cloud.ply"
    if not (
        manifest_path.is_file()
        and canary_path.is_file()
        and ply_path.is_file()
    ):
        return None
    try:
        manifest = load_run_manifest(manifest_path)
        canary = json.loads(canary_path.read_text(encoding="utf-8"))
        validate_run_manifest(manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    expected = {
        "iterations": request.iterations,
        "strategy": request.strategy,
        "sh_degree": request.sh_degree,
        "max_cap": request.max_cap,
        "resize_factor": request.resize_factor,
        "max_width": request.max_width,
        "tile_mode": request.tile_mode,
        "seed": request.seed,
        "profile_id": request.dronegs.profile_id,
        "optimizer_profile": request.dronegs.optimizer_profile,
        "pruning_policy": request.dronegs.pruning_policy,
        "raster_profile": request.dronegs.raster_profile,
        "effective_raster_profile": effective_raster_profile(
            request.dronegs.raster_profile,
            request.dronegs.optimizer_profile,
        ),
        "sh_degree_interval": request.dronegs.sh_degree_interval,
        "checkpoint_every": request.dronegs.checkpoint_every,
        "test_every": request.dronegs.test_every,
        "test_split": request.dronegs.test_split,
        "test_guard_percent": request.dronegs.test_guard_percent,
        "topology_cooldown_iterations": request.dronegs.topology_cooldown,
        "photometric_finish_iterations": request.dronegs.photometric_finish,
        "photometric_final_mse_percent": (
            request.dronegs.photometric_mse_percent
        ),
    }
    parameters = manifest.get("parameters", {})
    if (
        manifest.get("contract_version") != 1
        or manifest.get("status") != "completed"
        or manifest.get("trainer_binary_sha256") != trainer_binary_sha256
        or manifest.get("dataset", {}).get("fingerprint")
        != request.dataset_fingerprint
        or canary.get("status") != "passed"
        or canary.get("minimum_psnr") != request.dronegs.canary_min_psnr
        or canary.get("minimum_ssim") != request.dronegs.canary_min_ssim
        or any(parameters.get(key) != value for key, value in expected.items())
        or not manifest_matches_ply(manifest, ply_path)
    ):
        return None
    return TrainingResult(
        backend="dronegs",
        ply_path=ply_path,
        manifest_path=manifest_path,
        effective_seed=request.seed,
    )


def _quarantine_incompatible_dronegs_output(
    output_path: str | Path,
) -> Path | None:
    """Move an incompatible result aside so retraining remains recoverable."""

    output = Path(output_path)
    if not output.is_dir() or not any(output.iterdir()):
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    quarantine_root = (
        output.parent.parent
        / ".incompatible"
        / output.parent.name
    )
    quarantine_root.mkdir(parents=True, exist_ok=True)
    candidate = quarantine_root / f"{output.name}-{stamp}"
    suffix = 1
    while candidate.exists():
        candidate = quarantine_root / f"{output.name}-{stamp}-{suffix}"
        suffix += 1
    output.rename(candidate)
    return candidate


def generate_gaussian_orthophoto(
    dense_path: str,
    ortho_file: str,
    utm_crs: str,
    vol_id: str = "vol",
    transform_file: str = None,
    report_fn=None,
    resolution: float = 0.02,
    # Gaussian-specific params
    iterations: int = DRONEGS_PRODUCTION_PROFILE_V1.iterations,
    partition_m: int = 1,
    partition_n: int = 1,
    partition_overlap: float = 0.20,
    sh_degree: int = 3,
    fagk: bool = True,
    checkpoint_dir: str = None,
    data_factor: int = DRONEGS_PRODUCTION_PROFILE_V1.data_factor,
    max_width: int = DRONEGS_PRODUCTION_PROFILE_V1.max_width,
    tile_mode: int = DRONEGS_PRODUCTION_PROFILE_V1.tile_mode,
    cap_max: int = DRONEGS_PRODUCTION_PROFILE_V1.cap_max,
    filter_enabled: bool = True,
    filter_max_scale: float = 1.0,
    filter_dist_multiplier: float = 1.0,
    filter_opacity_threshold: float = 0.005,
    filter_needle_ratio: float = 0.0,
    filter_sor: bool = False,
    filter_sor_sigma: float = 4.0,
    filter_cc: bool = False,
    filter_z_floater: bool = False,
    verbose: bool = False,
    trainer_backend: str | None = None,
    training_seed: int = DRONEGS_PRODUCTION_PROFILE_V1.seed,
    dronegs_profile_id: str = DRONEGS_PRODUCTION_PROFILE_V1.profile_id,
    dronegs_optimizer_profile: str = (
        DRONEGS_PRODUCTION_PROFILE_V1.optimizer_profile
    ),
    dronegs_pruning_policy: str = (
        DRONEGS_PRODUCTION_PROFILE_V1.pruning_policy
    ),
    dronegs_raster_profile: str = (
        DRONEGS_PRODUCTION_PROFILE_V1.raster_profile
    ),
    dronegs_sh_degree_interval: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.sh_degree_interval
    ),
    dronegs_topology_cooldown: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.topology_cooldown
    ),
    dronegs_photometric_finish: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.photometric_finish
    ),
    dronegs_photometric_mse_percent: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.photometric_mse_percent
    ),
    dronegs_checkpoint_every: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.checkpoint_every
    ),
    dronegs_test_every: int = DRONEGS_PRODUCTION_PROFILE_V1.test_every,
    dronegs_test_split: str = DRONEGS_PRODUCTION_PROFILE_V1.test_split,
    dronegs_test_guard_percent: int = (
        DRONEGS_PRODUCTION_PROFILE_V1.test_guard_percent
    ),
    dronegs_canary_min_psnr: float = (
        DRONEGS_PRODUCTION_PROFILE_V1.canary_min_psnr
    ),
    dronegs_canary_min_ssim: float = (
        DRONEGS_PRODUCTION_PROFILE_V1.canary_min_ssim
    ),
    cancellation_check=None,
    checkpoint_callback=None,
):
    """
    Generate a True Digital Orthophoto Map using 3D Gaussian Splatting.

    Parameters
    ----------
    dense_path : str
        COLMAP dense workspace (contains sparse/, images/, stereo/).
    ortho_file : str
        Output GeoTIFF path.
    utm_crs : str
        Coordinate reference system (e.g. 'EPSG:32631').
    vol_id : str
        Volume ID for progress reporting.
    transform_file : str, optional
        Path to alignment_transform.json.
    report_fn : callable, optional
        Progress callback: report_fn(vol_id, step, progress, msg).
    resolution : float
        Ground sample distance in metres.
    iterations : int
        Training iterations per cell.
    partition_m, partition_n : int
        Grid partition dimensions (1×1 = no partition).
    partition_overlap : float
        Overlap fraction for partitioning.
    sh_degree : int
        Maximum spherical harmonics degree.
    fagk : bool
        Enable Fully Anisotropic Gaussian Kernel.
    checkpoint_dir : str, optional
        Directory for training checkpoints.
    data_factor : int
        Trainer image downscaling factor (1, 2, 4, or 8).
    max_width : int
        Maximum training image dimension after downscaling.
    tile_mode : int
        Backend memory-saving tile mode (1, 2, or 4).
    cap_max : int
        Maximum Gaussian count for MRNF strategy.
    trainer_backend : str, optional
        ``dronegs``. The environment variable
        DRONEAI_GAUSSIAN_BACKEND may override it.
    training_seed : int
        Requested base seed; each partition receives the base plus its index.
    dronegs_* :
        Native convergence controls. Defaults reproduce the Albagnac dev.45
        production profile.
    """
    import cupy as cp

    from .gaussian_model import GaussianModel
    from .merge import merge_models
    from .ortho_renderer import render_orthophoto

    # Ensure any stale CUDA allocations from a previous crashed run are freed
    import gc
    gc.collect()
    cp.get_default_memory_pool().free_all_blocks()
    try:
        free_bytes, total_bytes = cp.cuda.Device(0).mem_info
        vram_total = total_bytes / (1024 ** 3)
        vram_free = free_bytes / (1024 ** 3)
        dev_name = cp.cuda.runtime.getDeviceProperties(0)["name"]
        _report(vol_id, "GAUSS", 0,
                f"Starting Gaussian Splatting on {dev_name} "
                f"({vram_free:.1f}/{vram_total:.1f} GB free)", report_fn)
    except Exception:
        _report(vol_id, "GAUSS", 0, "Starting Gaussian Splatting", report_fn)

    if checkpoint_dir is None:
        checkpoint_dir = str(Path(ortho_file).parent / "gaussian_checkpoints")
    backend = resolve_training_backend(trainer_backend)
    trainer_binary_sha256 = backend.binary_sha256()

    # --- 1. Load COLMAP reconstruction ---
    _report(vol_id, "GAUSS", 5, "Loading COLMAP reconstruction…", report_fn)

    train_cameras, test_cameras, point_cloud, transform_data = load_colmap_reconstruction(
        dense_path, transform_file,
    )

    # --- 1b. Extract EXIF altitudes ---
    images_dir = os.path.join(dense_path, "images")
    exif_altitudes = extract_exif_altitudes(images_dir)
    # Map to cameras by image_name
    cam_alts = [exif_altitudes.get(cam.image_name, None) for cam in train_cameras]
    # Use mean of available altitudes
    valid_alts = [a for a in cam_alts if a is not None]
    mean_exif_alt = np.mean(valid_alts) if valid_alts else None

    # --- 1c. Compute COLMAP→metric scale factor from GPS ---
    # With a Sim3 transform the model is already metric after alignment.
    # Without one (PCA-only path), COLMAP units are arbitrary — we derive the
    # metres-per-unit ratio from GPS so the user can specify GSD in real metres.
    if transform_data:
        colmap_to_meters = float(transform_data.get("scale", 1.0))
    else:
        colmap_to_meters = compute_colmap_scale(train_cameras, images_dir, utm_crs)
        _report(vol_id, "GAUSS", 6,
                f"COLMAP scale: 1 unit = {colmap_to_meters:.2f} m (from GPS)",
                report_fn)

    # NOTE: We do NOT apply Sim3 before training. Training stays in COLMAP
    # local coordinates for float32 numerical stability.
    # Sim3 is applied AFTER training to transform the Gaussian model to
    # geo-aligned coordinates for the orthophoto rendering.

    _report(vol_id, "GAUSS", 10,
            f"Loaded {len(train_cameras)} cameras, {point_cloud.points.shape[0]} points",
            report_fn)

    scene = build_scene_info(train_cameras, test_cameras, point_cloud,
                             dense_path=dense_path)

    # --- 2. Partition ---
    use_partition = partition_m > 1 or partition_n > 1
    if use_partition:
        _report(vol_id, "GAUSS", 12,
                f"Partitioning scene into {partition_m}×{partition_n} cells…", report_fn)
        cells = partition_scene(scene, partition_m, partition_n, partition_overlap)
        _report(vol_id, "GAUSS", 15,
                f"Created {len(cells)} active cells", report_fn)
    else:
        cells = [(None, scene)]

    # --- 3. Train per cell through the stable backend boundary ---
    cell_models = []
    n_cells = len(cells)

    for i, (cell_bounds, cell_scene) in enumerate(cells):
        cell_label = f"cell_{i}" if use_partition else "full"
        pct_start = 15 + int(65 * i / n_cells)
        pct_end = 15 + int(65 * (i + 1) / n_cells)

        _report(vol_id, "GAUSS", pct_start,
                f"[{backend.name} MRNF] Training {cell_label}: "
                f"{len(cell_scene.train_cameras)} cameras, "
                f"{cell_scene.point_cloud.points.shape[0]} points",
                report_fn)

        # Prepare per-cell COLMAP data for the selected trainer.
        cell_output = os.path.join(checkpoint_dir, cell_label)
        sparse_dir = os.path.join(dense_path, "sparse", "0")
        if not os.path.isdir(sparse_dir):
            sparse_dir = os.path.join(dense_path, "sparse")
        images_dir_path = os.path.join(dense_path, "images")

        if use_partition:
            # Export filtered COLMAP subset for this cell
            cell_workspace = os.path.join(checkpoint_dir, f"{cell_label}_workspace")
            camera_names = [c.image_name for c in cell_scene.train_cameras]
            export_colmap_subset(
                source_sparse_dir=sparse_dir,
                target_dir=cell_workspace,
                camera_names=camera_names,
                images_dir=images_dir_path,
            )
            training_data_path = cell_workspace
        else:
            training_data_path = dense_path

        checkpoint_path = os.path.join(cell_output, "training.ckpt")
        resume_from = (
            checkpoint_path
            if os.path.isfile(checkpoint_path)
            and not os.path.isfile(os.path.join(cell_output, "trainer_run.json"))
            else None
        )
        if resume_from:
            _report(
                vol_id,
                "GAUSS",
                pct_start,
                f"[DroneGS] Resuming {cell_label} from its validated checkpoint",
                report_fn,
            )

        dataset_identity = compute_dataset_identity(training_data_path)
        training_request = TrainingRequest(
            data_path=training_data_path,
            output_path=cell_output,
            iterations=iterations,
            strategy="mrnf",
            sh_degree=sh_degree,
            max_cap=cap_max,
            resize_factor=data_factor,
            max_width=max_width,
            tile_mode=tile_mode,
            # The validated production profile is deterministic across full
            # and partitioned runs. Cell identity already lives in its
            # dataset fingerprint/output path; changing the seed silently
            # violates the DroneGS V1 request contract.
            seed=training_seed,
            dataset_fingerprint=dataset_identity.fingerprint,
            dronegs=DroneGSTuning(
                profile_id=dronegs_profile_id,
                optimizer_profile=dronegs_optimizer_profile,
                pruning_policy=dronegs_pruning_policy,
                raster_profile=dronegs_raster_profile,
                sh_degree_interval=dronegs_sh_degree_interval,
                topology_cooldown=min(
                    dronegs_topology_cooldown,
                    max(1, iterations // 5),
                ),
                photometric_finish=min(
                    dronegs_photometric_finish,
                    max(1, iterations // 5),
                ),
                photometric_mse_percent=dronegs_photometric_mse_percent,
                checkpoint_every=dronegs_checkpoint_every,
                resume_from=resume_from,
                test_every=dronegs_test_every,
                test_split=dronegs_test_split,
                test_guard_percent=dronegs_test_guard_percent,
                save_eval_images=dronegs_test_every > 0,
                canary_min_psnr=dronegs_canary_min_psnr,
                canary_min_ssim=dronegs_canary_min_ssim,
            ),
        )

        def make_training_reporter(pct_s, pct_e, vid, rfn, total):
            def reporter(it, loss_val, n_gauss):
                pct = pct_s + int((pct_e - pct_s) * it / max(1, total))
                _report(vid, "GAUSS", pct,
                        f"[MRNF] iter {it}: loss={loss_val:.4f}, N={n_gauss}", rfn)
            return reporter

        training_result = _reusable_dronegs_result(
            training_request,
            trainer_binary_sha256=trainer_binary_sha256,
        )
        if training_result is None:
            if training_request.dronegs.resume_from is None:
                quarantined = _quarantine_incompatible_dronegs_output(
                    training_request.output_path
                )
                if quarantined is not None:
                    _report(
                        vol_id,
                        "GAUSS",
                        pct_start,
                        (
                            "[DroneGS] Incompatible prior output preserved at "
                            f"{quarantined}; starting a clean training run."
                        ),
                        report_fn,
                    )
            training_result = backend.train(
                training_request,
                report_fn=make_training_reporter(
                    pct_start, pct_end, vol_id, report_fn, iterations,
                ),
                verbose=verbose,
                cancellation_check=cancellation_check,
                checkpoint_fn=checkpoint_callback,
            )
        else:
            _report(
                vol_id,
                "GAUSS",
                pct_end,
                f"[DroneGS] Reusing completed, canary-approved {cell_label}",
                report_fn,
            )
        ply_path = str(training_result.ply_path)

        # Load the exported PLY into our GaussianModel
        model = GaussianModel(sh_degree=sh_degree, fagk_enabled=fagk)
        model.load_ply(ply_path)
        _report(vol_id, "GAUSS", pct_end,
                f"[{backend.name}] Loaded {model.num_gaussians} Gaussians from {ply_path}",
                report_fn)

        cell_models.append((cell_bounds, model))

    # --- 4. Merge ---
    if use_partition and len(cell_models) > 1:
        _report(vol_id, "GAUSS", 82, "Merging cell models…", report_fn)
        cam_pos = np.stack([c.T for c in train_cameras])
        pts_xy = np.concatenate([point_cloud.points[:, :2], cam_pos[:, :2]])
        x_range = (float(pts_xy[:, 0].min()), float(pts_xy[:, 0].max()))
        y_range = (float(pts_xy[:, 1].min()), float(pts_xy[:, 1].max()))

        merged_model = merge_models(
            cell_models, x_range, y_range,
            partition_m, partition_n, partition_overlap,
        )
        _report(vol_id, "GAUSS", 85,
                f"Merged model: {merged_model.num_gaussians} Gaussians", report_fn)
    else:
        merged_model = cell_models[0][1]

    # Save final checkpoint (in local COLMAP coordinates)
    final_ply = os.path.join(checkpoint_dir, "final.ply")
    os.makedirs(checkpoint_dir, exist_ok=True)
    # Ensure active_sh_degree is set correctly (train() sets it, but merging
    # or loading from PLY can reset it to 0).
    merged_model.active_sh_degree = sh_degree
    merged_model.save_ply(final_ply)
    _report(vol_id, "GAUSS", 88, f"Saved final model: {final_ply}", report_fn)

    # --- Free training data before rendering (keep point_cloud for extent) ---
    # Save cameras for potential PCA auto-alignment (lightweight list of R, T)
    _all_cameras = train_cameras
    del cells, cell_models
    import gc
    gc.collect()
    cp.get_default_memory_pool().free_all_blocks()

    # --- 5. Geo-alignment ---
    # IMPORTANT: We split the Sim3 into rotation+scale (applied to the model)
    # and translation (kept separate as float64).  Applying the full UTM
    # translation (~10^6) to float32 Gaussian positions causes catastrophic
    # precision loss.  E.g. Y ≈ 4,702,500 → float32 ULP = 0.5 m = 25 px
    # banding at GSD = 0.02 m.  Instead the model stays centred near zero
    # and the translation is folded into the ortho camera and GeoTIFF origin.
    #
    # For the PCA path (no Sim3 transform), we NO LONGER rotate the model.
    # Rotating positions+quaternions without rotating SH coefficients causes
    # a colour mismatch when rendering from nadir.  Instead the model stays
    # in the original COLMAP coordinate frame and R_geo is passed to the
    # orthographic renderer to orient the virtual nadir camera correctly.
    geo_origin = np.zeros(3, dtype=np.float64)   # will be added to GeoTIFF
    R_geo = None  # rotation COLMAP→geo for renderer (PCA path only)
    if transform_data:
        _report(vol_id, "GAUSS", 89, "Applying geo-alignment to Gaussian model…", report_fn)
        # Apply only scale + rotation to the model (keeps coords near 0)
        R = cp.array(transform_data["R"], dtype=cp.float32)
        s = float(transform_data["scale"])
        t_f64 = np.array(transform_data["t"], dtype=np.float64)

        import math as _math
        merged_model._xyz = (s * (R @ merged_model._xyz.T)).T
        merged_model._scaling += _math.log(s)
        R_quat = merged_model._matrix_to_quaternion(cp.asnumpy(R))
        R_quat_cp = cp.array(R_quat, dtype=cp.float32)
        merged_model._rotation = merged_model._quaternion_multiply(
            R_quat_cp[None, :], merged_model._rotation,
        )
        geo_origin = t_f64           # stored as float64, used for GeoTIFF only

        # Keep transformed camera positions for spatial filtering
        geo_cam_positions = apply_sim3_to_points(
            np.array([c.T for c in _all_cameras], dtype=np.float64), transform_data)
    else:
        # --- PCA path: compute R_geo for renderer (DON'T rotate the model!) ---
        # The model stays in COLMAP frame.  R_geo tells the ortho renderer
        # which direction is "down" without breaking SH evaluation.
        _report(vol_id, "GAUSS", 89, "Computing PCA nadir direction…", report_fn)
        from .pca_alignment import compute_pca_rotation

        cam_positions = np.array([c.T for c in _all_cameras], dtype=np.float64)
        R_align, angle_deg = compute_pca_rotation(_all_cameras, point_cloud.points)
        R_geo = R_align.astype(np.float32)
        _report(vol_id, "GAUSS", 89,
                f"PCA nadir direction: {angle_deg:.1f}° from Z (using R_geo for rendering)",
                report_fn)

        # Camera positions in geo-aligned frame (for GeoTIFF origin computation)
        geo_cam_positions = (R_align @ cam_positions.T).T

        # Compute geo_origin for GeoTIFF
        from .exif_altitude import extract_exif_gps
        from pyproj import Transformer as _Transformer
        _gps = extract_exif_gps(images_dir)
        _t_proj = _Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
        _utm_pts = []
        for cam in _all_cameras:
            g = _gps.get(cam.image_name)
            if g is not None:
                e, n = _t_proj.transform(g[1], g[0])
                _utm_pts.append([e, n, mean_exif_alt or 0.0])
        if _utm_pts:
            _gps_centroid = np.mean(_utm_pts, axis=0).astype(np.float64)
            _model_centroid = geo_cam_positions.mean(axis=0) * colmap_to_meters
            geo_origin = _gps_centroid - _model_centroid
            _report(vol_id, "GAUSS", 89,
                    f"GeoTIFF origin from GPS: E={geo_origin[0]:.2f}, N={geo_origin[1]:.2f}",
                    report_fn)
        del _gps, _utm_pts
    del point_cloud

    # Camera positions in the same coordinate frame as the model.
    # Sim3 path: geo_cam = s*R@cam + t (full UTM), model = s*R@cam → subtract t.
    # PCA path:  model is in COLMAP frame → use raw COLMAP camera positions.
    # geo_origin is only used for the GeoTIFF mapping, NOT for spatial filtering.
    if transform_data:
        local_cam_positions = geo_cam_positions - geo_origin
    else:
        local_cam_positions = np.array([c.T for c in _all_cameras], dtype=np.float64)

    # --- 5b–e. Filter outlier Gaussians ---
    if not filter_enabled:
        _report(vol_id, "GAUSS", 89, f"Filtering disabled — keeping all {merged_model.num_gaussians} Gaussians", report_fn)
    else:
        from .model_filtering import filter_gaussians
        _report(vol_id, "GAUSS", 89, "Filtering Gaussians…", report_fn)
        filter_gaussians(
            merged_model,
            local_cam_positions,
            max_scale=filter_max_scale,
            dist_multiplier=filter_dist_multiplier,
            opacity_threshold=filter_opacity_threshold,
            needle_ratio=filter_needle_ratio,
            sor_sigma=filter_sor_sigma,
            sor_enabled=filter_sor,
            cc_enabled=filter_cc,
            z_floater_enabled=filter_z_floater,
            R_geo=R_geo,
            report_fn=lambda msg: _report(vol_id, "GAUSS", 89, msg, report_fn),
        )
        _report(vol_id, "GAUSS", 89, f"After filtering: {merged_model.num_gaussians} Gaussians", report_fn)

    # Re-save final.ply after all filters so the checkpoint is clean.
    merged_model.save_ply(final_ply)
    _report(vol_id, "GAUSS", 95,
            f"Saved filtered model: {final_ply} ({merged_model.num_gaussians} Gaussians)",
            report_fn)
    del _all_cameras, scene

    # Define rendering extent in local coordinates.
    # Use the Gaussian model's own percentile-clipped position bounds rather
    # than the raw COLMAP point cloud bounds.  The COLMAP cloud often contains
    # outlier points far from the scene which inflate the Z range: this causes
    # "sky" Gaussians (large scale, high Z) to enter the near/far frustum and
    # produce a uniform haze when viewed from the ortho camera.
    from .ortho_renderer import compute_ortho_extent as _compute_extent
    model_extent = _compute_extent(merged_model, pad=2.0, R_geo=R_geo)
    render_extent = model_extent

    gc.collect()
    cp.get_default_memory_pool().free_all_blocks()

    # --- 6. Render orthophoto ---
    # Convert metric GSD (metres/pixel) to model-unit GSD for the renderer.
    # With Sim3: model is metric after scale*R → local_gsd = resolution.
    # With PCA only: model stays in COLMAP units → divide by scale factor.
    if transform_data:
        local_gsd = resolution
        geo_gsd = resolution
    else:
        local_gsd = resolution / colmap_to_meters
        geo_gsd = resolution  # GeoTIFF pixel size is always in CRS metres
    _report(vol_id, "GAUSS", 96,
            f"Rendering orthographic TDOM at {resolution} m/px "
            f"(local GSD={local_gsd:.6f})…", report_fn)
    result = render_orthophoto(
        merged_model, gsd=local_gsd, extent=render_extent,
        R_geo=R_geo,
    )

    rgb = result["rgb"]
    height = result["height"]
    x_min, x_max, y_min, y_max = result["extent"]
    H, W = rgb.shape[:2]
    _report(vol_id, "GAUSS", 97,
            f"Orthophoto rendered: {W}x{H} px at GSD={resolution} m/px", report_fn)

    # --- 7. Write GeoTIFF ---
    # Translate the local-coordinate extent back to geographic (UTM) coords.
    # With Sim3: model is metric, geo_origin is the Sim3 translation (float64).
    # With PCA: model is in COLMAP units, scale to metres + add GPS-derived origin.
    if transform_data:
        geo_x_min = float(np.float64(x_min) + geo_origin[0])
        geo_y_max = float(np.float64(y_max) + geo_origin[1])
    else:
        geo_x_min = float(np.float64(x_min) * colmap_to_meters + geo_origin[0])
        geo_y_max = float(np.float64(y_max) * colmap_to_meters + geo_origin[1])

    # --- Altitude correction: shift model Z to match mean EXIF altitude if available ---
    # For PCA path, height is in COLMAP units — scale to metres first.
    if not transform_data and colmap_to_meters != 1.0:
        height = height * colmap_to_meters
    if mean_exif_alt is not None:
        z_offset = mean_exif_alt - np.mean(height)
        height = height + z_offset
        _report(vol_id, "GAUSS", 97, f"Shifted height map by {z_offset:.2f} m to match mean EXIF altitude {mean_exif_alt:.2f}", report_fn)
    else:
        _report(vol_id, "GAUSS", 97, "No EXIF altitudes found; using model Z for height map.", report_fn)

    _report(vol_id, "GAUSS", 98, "Writing GeoTIFF\u2026", report_fn)

    height_file = str(Path(ortho_file).with_suffix(".height.tif"))
    write_geotiff(
        output_path=ortho_file,
        rgb=rgb,
        x_min=geo_x_min, y_max=geo_y_max, gsd=resolution,
        crs=utm_crs,
        height_map=height,
        height_output_path=height_file,
    )

    _report(vol_id, "GAUSS", 100,
            f"Done. Orthomosaic: {ortho_file}, Height: {height_file}", report_fn)

    return {
        "ortho_file": ortho_file,
        "height_file": height_file,
        "checkpoint_dir": checkpoint_dir,
        "final_ply": final_ply,
        "width": W,
        "height": H,
        "gsd": resolution,
        "n_gaussians": merged_model.num_gaussians,
    }
