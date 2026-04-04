"""
Main entry point for Gaussian Splatting orthophoto generation.

Provides `generate_gaussian_orthophoto()` with the same interface pattern
as `generate_true_orthophoto_pytorch()` from ortho_dsm.py, making it easy
to swap in from the existing pipeline.

Pipeline:
  1. Load COLMAP reconstruction + alignment transform
  2. Partition scene (VastGaussian, if m×n > 1×1)
  3. Train Gaussian model per cell (with depth regularisation)
  4. Merge cell models
  5. Render orthographic TDOM
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
from .train import train, TrainConfig
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
    lambda_depth: float = 0.1,
    checkpoint_dir: str = None,
    data_factor: int = 1,
    strategy: str = "mcmc",
    cap_max: int = 1_000_000,
    # Disabled: ortho_reg has a coordinate-system mismatch with scene
    # normalisation (cameras in COLMAP coords vs scene in normalised space).
    # TODO: fix _ortho_coverage_loss to pass normalised camera positions.
    ortho_reg: float = 0.0,
    filter_enabled: bool = True,
    filter_sor: bool = True,
    filter_cc: bool = True,
    filter_z_floater: bool = True,
    filter_needle_ratio: float = 50.0,
    filter_sor_sigma: float = 4.0,
    nadir_finetune_iters: int = 3000,
    nadir_finetune_mode: str = "full",
    nadir_finetune_angle: float = 15.0,
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
    lambda_depth : float
        Depth regularisation weight.
    checkpoint_dir : str, optional
        Directory for training checkpoints.
    data_factor : int
        Image downscaling factor for training (4 = quarter-res, fast).
    strategy : str
        Densification strategy: "mcmc" (bounded, recommended) or "default".
    cap_max : int
        Maximum Gaussian count for MCMCStrategy.
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

    # --- 3. Train per cell ---
    cell_models = []
    n_cells = len(cells)

    for i, (cell_bounds, cell_scene) in enumerate(cells):
        cell_label = f"cell_{i}" if use_partition else "full"
        pct_start = 15 + int(65 * i / n_cells)
        pct_end = 15 + int(65 * (i + 1) / n_cells)

        _report(vol_id, "GAUSS", pct_start,
                f"Training {cell_label}: {len(cell_scene.train_cameras)} cameras, "
                f"{cell_scene.point_cloud.points.shape[0]} points",
                report_fn)

        model = GaussianModel(sh_degree=sh_degree, fagk_enabled=fagk)
        model = model.to(device)
        init_opa = 0.5 if strategy.lower() == "mcmc" else 0.1
        # gsplat MCMC uses init_scale=0.1 even for normalised scenes:
        # smaller initial Gaussians give sharper gradient localisation.
        init_scale = 0.1 if strategy.lower() == "mcmc" else 1.0
        model.init_from_point_cloud(cell_scene.point_cloud, cell_scene.scene_radius,
                                    init_opa=init_opa, init_scale=init_scale)

        # --- VRAM-aware training config ---
        effective_cap = cap_max
        effective_ortho_crop = 256
        progressive = None

        # Probe available GPU VRAM for progressive schedule on tiny GPUs
        vram_gb = None
        if torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)

        if vram_gb is not None and vram_gb < 12:
            # Enable progressive schedule for ≤12 GB GPUs with large images
            # Probe max image dimension from the first camera
            try:
                from PIL import Image as PILImage
                first_cam = cell_scene.train_cameras[0]
                max_dim = max(first_cam.width, first_cam.height)
            except Exception:
                max_dim = 0

            if max_dim > 1200 and data_factor <= 2:
                # Build schedule: start coarse, ramp to target data_factor
                progressive = []
                if max_dim > 3000:
                    progressive.append((0.0, 8))
                    progressive.append((0.25, 4))
                    progressive.append((0.50, 2))
                    if data_factor <= 1:
                        progressive.append((0.75, 1))
                elif max_dim > 2000:
                    progressive.append((0.0, 4))
                    progressive.append((0.35, 2))
                    if data_factor <= 1:
                        progressive.append((0.70, 1))
                else:  # 1200 < max_dim <= 2000
                    progressive.append((0.0, 2))
                    if data_factor <= 1:
                        progressive.append((0.50, 1))
                _report(vol_id, "GAUSS", pct_start,
                        f"Progressive schedule: {progressive} (VRAM={vram_gb:.1f}GB, max_dim={max_dim}px)",
                        report_fn)

        cfg = TrainConfig(
            iterations=iterations,
            data_factor=data_factor,
            progressive_schedule=progressive,
            sh_degree=sh_degree,
            strategy=strategy,
            cap_max=effective_cap,
            ortho_reg=ortho_reg,
            ortho_crop_px=effective_ortho_crop,
            output_dir=os.path.join(checkpoint_dir, cell_label),
        )

        def make_train_reporter(pct_s, pct_e, vid, rfn):
            def reporter(it, loss_val, n_gauss):
                pct = pct_s + int((pct_e - pct_s) * it / max(1, cfg.iterations))
                _report(vid, "GAUSS", pct,
                        f"iter {it}: loss={loss_val:.4f}, N={n_gauss}", rfn)
            return reporter

        model = train(cell_scene, model, cfg,
                      report_fn=make_train_reporter(pct_start, pct_end, vol_id, report_fn))

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
        from numpy.linalg import svd as _svd

        cam_positions = np.array([c.T for c in _all_cameras], dtype=np.float64)
        centroid = cam_positions.mean(axis=0)
        _, S_pca, Vt_pca = _svd(cam_positions - centroid, full_matrices=False)

        up_est = Vt_pca[2].astype(np.float64)  # smallest eigenvalue direction
        up_est = up_est / max(np.linalg.norm(up_est), 1e-9)

        # Build rotation that maps up_est → [0, 0, 1]
        target = np.array([0.0, 0.0, 1.0])
        v = np.cross(up_est, target)
        c_dot = np.dot(up_est, target)
        if np.linalg.norm(v) < 1e-8:
            # Already aligned (or exactly anti-parallel)
            R_align = np.eye(3) if c_dot > 0 else np.diag([1.0, -1.0, -1.0])
        else:
            vx = np.array([[0, -v[2], v[1]],
                           [v[2], 0, -v[0]],
                           [-v[1], v[0], 0]])
            R_align = np.eye(3) + vx + vx @ vx / (1.0 + c_dot)

        # Post-rotation check: cameras must be ABOVE the scene (drone data).
        rot_cam_z = (R_align @ cam_positions.T)[2, :].mean()
        rot_scene_z = (R_align @ point_cloud.points.T)[2, :].mean()
        if rot_cam_z < rot_scene_z:
            R_align = np.diag([1.0, -1.0, -1.0]) @ R_align
            c_dot = -c_dot

        R_align = R_align.astype(np.float32)
        R_geo = R_align  # pass to renderer
        angle_deg = np.degrees(np.arccos(np.clip(abs(c_dot), -1, 1)))
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

    # --- 5b. Compute camera-based proximity threshold for Gaussian filtering ---
    from scipy.spatial import cKDTree as _cKDTree
    from scipy.spatial.distance import pdist as _pdist
    _cam_tree = _cKDTree(local_cam_positions)
    # Use scene diameter (max inter-camera distance) as threshold — no
    # legitimate Gaussian should be farther from all cameras than the cameras
    # are from each other.
    _max_cam_dist = float(np.max(_pdist(local_cam_positions)))

    # --- 5c. Filter outlier Gaussians ---
    if not filter_enabled:
        _report(vol_id, "GAUSS", 89, f"Filtering disabled — keeping all {merged_model.num_gaussians} Gaussians", report_fn)
    else:
        # The model is in local coordinates (centred near zero).
        # Filter Gaussians by proximity to nearest camera — same adaptive
        # threshold used for the sparse point cloud in colmap_loader.
        xyz = merged_model.positions.detach()
        xyz_np = xyz.cpu().numpy()
        gauss_dists, _ = _cam_tree.query(xyz_np, k=1)
        in_bounds = torch.tensor(gauss_dists <= _max_cam_dist,
                                 device=merged_model._xyz.device)
        # Remove nearly transparent Gaussians
        visible = merged_model.opacity.squeeze(-1).detach() > 0.05

        # Remove highly elongated Gaussians (needle artifacts in ortho view).
        if filter_needle_ratio > 0:
            log_scales = merged_model._scaling.detach()
            sorted_log, _ = log_scales.sort(dim=-1)
            aniso_ratio = (sorted_log[:, 2] - sorted_log[:, 0]).exp()
            not_needle = aniso_ratio <= filter_needle_ratio
        else:
            not_needle = torch.ones(len(xyz), dtype=torch.bool,
                                    device=merged_model._xyz.device)

        keep = in_bounds & visible & not_needle
        n_before = merged_model.num_gaussians
        merged_model.filter_by_mask(keep)
        n_after = merged_model.num_gaussians
        _report(vol_id, "GAUSS", 89,
                f"Filtered: {n_before} → {n_after} Gaussians "
                f"(removed {n_before - n_after} outliers/floaters, "
                f"cam_dist_thresh={_max_cam_dist:.2f}, needle_ratio={filter_needle_ratio})",
                report_fn)
        del xyz, xyz_np, gauss_dists, in_bounds, visible, not_needle, keep
        if filter_needle_ratio > 0:
            del log_scales, sorted_log, aniso_ratio

        # --- 5d. Statistical outlier removal (SOR) ---
        if filter_sor:
            from scipy.spatial import cKDTree

            xyz_np = merged_model.positions.detach().cpu().numpy()
            k_sor = 16
            tree = cKDTree(xyz_np)
            dists, idx = tree.query(xyz_np, k=k_sor + 1)  # +1: first is self (dist=0)
            mean_dists = dists[:, 1:].mean(axis=1)
            mu = mean_dists.mean()
            sigma = mean_dists.std()
            sor_thresh = mu + filter_sor_sigma * sigma
            sor_keep = torch.tensor(mean_dists <= sor_thresh, device=merged_model._xyz.device)
            n_before_sor = merged_model.num_gaussians
            merged_model.filter_by_mask(sor_keep)
            n_removed_sor = n_before_sor - merged_model.num_gaussians
            if n_removed_sor > 0:
                _report(vol_id, "GAUSS", 89,
                        f"SOR: removed {n_removed_sor} isolated Gaussians "
                        f"(k={k_sor}, sigma={filter_sor_sigma}, threshold={sor_thresh:.4f})",
                        report_fn)
            del xyz_np, tree, dists, idx, mean_dists, sor_keep

        # --- 5e. Connected-component filter ---
        if filter_cc:
            from scipy.sparse import csr_matrix
            from scipy.sparse.csgraph import connected_components
            from scipy.spatial import cKDTree

            k_cc = 16
            xyz_np = merged_model.positions.detach().cpu().numpy()
            N_cc = len(xyz_np)
            tree = cKDTree(xyz_np)
            _, idx_k = tree.query(xyz_np, k=k_cc + 1)
            rows = np.repeat(np.arange(N_cc), k_cc)
            cols = idx_k[:, 1:].ravel()
            adj = csr_matrix(
                (np.ones(len(rows), dtype=np.float32), (rows, cols)),
                shape=(N_cc, N_cc),
            )
            n_components, labels = connected_components(adj, directed=False)
            if n_components > 1:
                unique, counts = np.unique(labels, return_counts=True)
                largest_label = unique[counts.argmax()]
                cc_keep = torch.tensor(labels == largest_label, device=merged_model._xyz.device)
                n_before_cc = merged_model.num_gaussians
                merged_model.filter_by_mask(cc_keep)
                n_removed_cc = n_before_cc - merged_model.num_gaussians
                _report(vol_id, "GAUSS", 89,
                        f"CC filter: removed {n_removed_cc} floaters in "
                        f"{n_components - 1} disconnected clusters",
                        report_fn)
                del cc_keep
            del xyz_np, tree, idx_k, adj, labels

    # --- Z-floater removal ---
    if filter_z_floater:
        with torch.no_grad():
            if R_geo is not None:
                R_geo_t = torch.tensor(R_geo, dtype=torch.float32,
                                       device=merged_model._xyz.device)
                z_vals = (R_geo_t[2:3, :] @ merged_model._xyz.T).squeeze(0).detach()
            else:
                z_vals = merged_model._xyz[:, 2].detach()
            q25 = torch.quantile(z_vals, 0.25).item()
            q75 = torch.quantile(z_vals, 0.75).item()
            z_iqr = q75 - q25
            z_lo = q25 - 5.0 * z_iqr
            z_hi = q75 + 5.0 * z_iqr
            z_keep = (z_vals >= z_lo) & (z_vals <= z_hi)
            n_before_z = merged_model.num_gaussians
            merged_model.filter_by_mask(z_keep)
            n_removed_z = n_before_z - merged_model.num_gaussians
            if n_removed_z > 0:
                _report(vol_id, "GAUSS", 89,
                        f"Z-floater filter: removed {n_removed_z} sky/background Gaussians "
                        f"(Z outside [{z_lo:.2f}, {z_hi:.2f}])",
                        report_fn)
            del z_vals, z_keep

    # --- Nadir fine-tune (PCA path only) ---
    # In the PCA path the model stays in COLMAP frame.  The SH coefficients
    # were trained from mixed oblique/nadir views and can produce colour
    # artefacts when rendered from a pure nadir ortho camera.  We fine-tune
    # SH (and optionally scales + opacity) using only near-nadir training
    # images so the model adapts to the ortho view.
    if R_geo is not None and nadir_finetune_iters > 0 and nadir_finetune_mode != "off":
        from .train import nadir_finetune as _nadir_finetune_sh
        from .train import nadir_finetune_full as _nadir_finetune_full
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
