"""
Main entry point for Gaussian Splatting orthophoto generation.

Provides `generate_gaussian_orthophoto()` with the same interface pattern
as `generate_true_orthophoto_pytorch()` from ortho_dsm.py, making it easy
to swap in from the existing pipeline.

Pipeline:
  1. Load COLMAP reconstruction + alignment transform
  2. Partition scene (VastGaussian, if m×n > 1×1)
  3. Train Gaussian model per cell via LichtFeld MRNF (C++ headless)
  4. Merge cell models
  5. Render orthographic TDOM (gsplat rasterisation)
  6. Write GeoTIFF
"""
import json
import os
from pathlib import Path

import numpy as np
import torch

from .colmap_loader import (
    load_colmap_reconstruction,
    apply_sim3_to_points,
)
from .scene_info import build_scene_info
from .gaussian_model import GaussianModel
from .lichtfeld_trainer import (
    LichtFeldTrainConfig,
    train_with_lichtfeld,
    export_colmap_subset,
)
from .partition import partition_scene
from .merge import merge_models
from .ortho_renderer import render_orthophoto, compute_ortho_extent
from .geo_writer import write_geotiff
from .exif_altitude import extract_exif_altitudes, compute_colmap_scale


def _report(vol_id, step, progress, msg, report_fn):
    if report_fn:
        report_fn(vol_id, step, progress, log=msg)
    else:
        print(f"[{step} {progress}%] {msg}")


def generate_gaussian_orthophoto(
    dense_path: str,
    ortho_file: str,
    utm_crs: str,
    vol_id: str = "vol",
    transform_file: str = None,
    report_fn=None,
    resolution: float = 0.02,
    # Gaussian-specific params
    iterations: int = 30_000,
    partition_m: int = 1,
    partition_n: int = 1,
    partition_overlap: float = 0.20,
    sh_degree: int = 3,
    fagk: bool = True,
    checkpoint_dir: str = None,
    data_factor: int = 1,
    cap_max: int = 5_000_000,
    filter_enabled: bool = True,
    filter_max_scale: float = 1.0,
    filter_dist_multiplier: float = 1.0,
    filter_opacity_threshold: float = 0.005,
    filter_needle_ratio: float = 0.0,
    filter_sor: bool = False,
    filter_sor_sigma: float = 4.0,
    filter_cc: bool = False,
    filter_z_floater: bool = False,
    nadir_finetune_iters: int = 3000,
    nadir_finetune_mode: str = "full",
    nadir_finetune_angle: float = 15.0,
    verbose: bool = False,
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
        Training iterations per cell (LichtFeld MRNF).
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
        Image downscaling factor (used by nadir fine-tune).
    cap_max : int
        Maximum Gaussian count for MRNF strategy.
    nadir_finetune_iters : int
        Nadir fine-tune iterations (0 = skip). Default 3000.
    nadir_finetune_mode : str
        "full" = optimise SH + scales + opacity (recommended for ortho quality).
        "sh_only" = optimise only SH coefficients.
        "off" = skip nadir fine-tuning entirely.
    nadir_finetune_angle : float
        Maximum angle from nadir for camera selection during fine-tuning.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Ensure any stale CUDA allocations from a previous crashed run are freed
    if device.type == "cuda":
        import gc
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        vram_total = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        vram_free = (torch.cuda.get_device_properties(0).total_memory - torch.cuda.memory_allocated()) / (1024 ** 3)
        _report(vol_id, "GAUSS", 0,
                f"Starting Gaussian Splatting on {torch.cuda.get_device_name(0)} "
                f"({vram_free:.1f}/{vram_total:.1f} GB free)", report_fn)

    if checkpoint_dir is None:
        checkpoint_dir = str(Path(ortho_file).parent / "gaussian_checkpoints")

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

    # --- 3. Train per cell (LichtFeld MRNF) ---
    cell_models = []
    n_cells = len(cells)

    for i, (cell_bounds, cell_scene) in enumerate(cells):
        cell_label = f"cell_{i}" if use_partition else "full"
        pct_start = 15 + int(65 * i / n_cells)
        pct_end = 15 + int(65 * (i + 1) / n_cells)

        _report(vol_id, "GAUSS", pct_start,
                f"[LichtFeld MRNF] Training {cell_label}: "
                f"{len(cell_scene.train_cameras)} cameras, "
                f"{cell_scene.point_cloud.points.shape[0]} points",
                report_fn)

        # Prepare per-cell COLMAP data for LichtFeld
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
            lf_data_path = cell_workspace
        else:
            lf_data_path = dense_path

        lf_config = LichtFeldTrainConfig(
            iterations=iterations,
            strategy="mrnf",
            sh_degree=sh_degree,
            cap_max=cap_max,
            data_path=lf_data_path,
            output_path=cell_output,
            data_factor=data_factor,
        )

        def make_lf_reporter(pct_s, pct_e, vid, rfn, total):
            def reporter(it, loss_val, n_gauss):
                pct = pct_s + int((pct_e - pct_s) * it / max(1, total))
                _report(vid, "GAUSS", pct,
                        f"[MRNF] iter {it}: loss={loss_val:.4f}, N={n_gauss}", rfn)
            return reporter

        ply_path = train_with_lichtfeld(
            lf_config,
            report_fn=make_lf_reporter(pct_start, pct_end, vol_id, report_fn,
                                       iterations),
            verbose=verbose,
        )

        # Load the exported PLY into our GaussianModel
        model = GaussianModel(sh_degree=sh_degree, fagk_enabled=fagk)
        model.load_ply(ply_path)
        _report(vol_id, "GAUSS", pct_end,
                f"[LichtFeld] Loaded {model.num_gaussians} Gaussians from {ply_path}",
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
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

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
        R = torch.tensor(transform_data["R"], dtype=torch.float32,
                         device=merged_model._xyz.device)
        s = float(transform_data["scale"])
        t_f64 = np.array(transform_data["t"], dtype=np.float64)

        import math as _math
        merged_model._xyz.data = (s * (R @ merged_model._xyz.data.T)).T
        merged_model._scaling.data += _math.log(s)
        R_quat = merged_model._matrix_to_quaternion(R)
        merged_model._rotation.data = merged_model._quaternion_multiply(
            R_quat.unsqueeze(0), merged_model._rotation.data,
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

    # --- Nadir fine-tune (PCA path only) ---
    # In the PCA path the model stays in COLMAP frame.  The SH coefficients
    # were trained from mixed oblique/nadir views and can produce colour
    # artefacts when rendered from a pure nadir ortho camera.  We fine-tune
    # SH (and optionally scales + opacity) using only near-nadir training
    # images so the model adapts to the ortho view.
    if R_geo is not None and nadir_finetune_iters > 0 and nadir_finetune_mode != "off":
        from .nadir_finetune import nadir_finetune as _nadir_finetune_sh
        from .nadir_finetune import nadir_finetune_full as _nadir_finetune_full
        _report(vol_id, "GAUSS", 90,
                f"Nadir fine-tune ({nadir_finetune_mode}, {nadir_finetune_iters} iters, "
                f"angle≤{nadir_finetune_angle}°)…", report_fn)

        def _make_ft_reporter(pct_s, pct_e, total_iters):
            def _ft_report(it, loss, n):
                pct = pct_s + int((pct_e - pct_s) * it / max(1, total_iters))
                _report(vol_id, "GAUSS", pct,
                        f"fine-tune iter {it}/{total_iters}: loss={loss:.5f}, N={n}",
                        report_fn)
            return _ft_report

        if nadir_finetune_mode == "full":
            merged_model = _nadir_finetune_full(
                scene, merged_model,
                iterations=nadir_finetune_iters,
                data_factor=data_factor,
                max_angle_deg=nadir_finetune_angle,
                report_fn=_make_ft_reporter(90, 95, nadir_finetune_iters),
            )
        else:
            merged_model = _nadir_finetune_sh(
                scene, merged_model,
                iterations=nadir_finetune_iters,
                data_factor=data_factor,
                max_angle_deg=nadir_finetune_angle,
                report_fn=_make_ft_reporter(90, 95, nadir_finetune_iters),
            )
        _report(vol_id, "GAUSS", 95, "Nadir fine-tune complete.", report_fn)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # Re-save final.ply after all filters + fine-tune so the checkpoint is clean.
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
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

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
        merged_model, gsd=local_gsd, extent=render_extent, device=device,
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
