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
from .exif_altitude import extract_exif_altitudes


def _report(vol_id, step, progress, msg, report_fn):
    if report_fn:
        report_fn(vol_id, step, progress, msg)
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
    data_factor: int = 4,
    strategy: str = "mcmc",
    cap_max: int = 1_000_000,
    ortho_reg: float = 0.5,
    filter_enabled: bool = True,
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
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _report(vol_id, "GAUSS", 0, f"Starting Gaussian Splatting orthophoto on {device}", report_fn)

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
        model.init_from_point_cloud(cell_scene.point_cloud, cell_scene.scene_radius,
                                    init_opa=init_opa)

        # --- VRAM-aware training config ---
        effective_cap = cap_max
        effective_ortho_crop = 256
        progressive = None

        # Probe available GPU VRAM for progressive schedule on tiny GPUs
        vram_gb = None
        if torch.cuda.is_available():
            vram_gb = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)

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
    merged_model.save_ply(final_ply)
    _report(vol_id, "GAUSS", 88, f"Saved final model: {final_ply}", report_fn)

    # --- Free training data before rendering (keep point_cloud for extent) ---
    del cells, cell_models, scene, train_cameras, test_cameras
    import gc
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # --- 5. Apply Sim3 geo-alignment to model ---
    # IMPORTANT: We split the Sim3 into rotation+scale (applied to the model)
    # and translation (kept separate as float64).  Applying the full UTM
    # translation (~10^6) to float32 Gaussian positions causes catastrophic
    # precision loss.  E.g. Y ≈ 4,702,500 → float32 ULP = 0.5 m = 25 px
    # banding at GSD = 0.02 m.  Instead the model stays centred near zero
    # and the translation is folded into the ortho camera and GeoTIFF origin.
    geo_origin = np.zeros(3, dtype=np.float64)   # will be added to GeoTIFF
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

        # Compute scene extent from the geo-aligned COLMAP point cloud
        geo_pts = apply_sim3_to_points(point_cloud.points, transform_data)
    else:
        geo_pts = point_cloud.points
    del point_cloud

    # --- 5b. Compute point cloud bounds (needed for rendering extent) ---
    pad_m = 20.0  # metres of padding beyond point cloud bounds
    pc_min = geo_pts.min(axis=0)
    pc_max = geo_pts.max(axis=0)
    del geo_pts

    # Convert geo bounds → local (subtract geo_origin)
    local_pc_min = pc_min - geo_origin
    local_pc_max = pc_max - geo_origin

    # --- 5c. Filter outlier Gaussians ---
    if not filter_enabled:
        _report(vol_id, "GAUSS", 89, f"Filtering disabled — keeping all {merged_model.num_gaussians} Gaussians", report_fn)
    else:
        # The model is in local coordinates (centred near zero).
        # Filter using the geo-aligned point cloud bounds shifted to local coords.
        xyz = merged_model.positions.detach()
        scales = merged_model.scales.detach()
        max_scale = scales.max(dim=-1).values

        # Keep Gaussians within the padded point cloud bounding box (local coords)
        in_bounds = (
            (xyz[:, 0] >= local_pc_min[0] - pad_m) & (xyz[:, 0] <= local_pc_max[0] + pad_m) &
            (xyz[:, 1] >= local_pc_min[1] - pad_m) & (xyz[:, 1] <= local_pc_max[1] + pad_m) &
            (xyz[:, 2] >= local_pc_min[2] - pad_m) & (xyz[:, 2] <= local_pc_max[2] + pad_m)
        )
        # Remove nearly transparent Gaussians
        visible = merged_model.opacity.squeeze(-1).detach() > 0.05

        # Remove highly elongated Gaussians (needle artifacts in ortho view).
        log_scales = merged_model._scaling.detach()
        sorted_log, _ = log_scales.sort(dim=-1)
        aniso_ratio = (sorted_log[:, 2] - sorted_log[:, 0]).exp()  # max/min scale ratio
        not_needle = aniso_ratio <= 50.0  # remove extreme needles

        keep = in_bounds & visible & not_needle
        n_before = merged_model.num_gaussians
        merged_model.filter_by_mask(keep)
        n_after = merged_model.num_gaussians
        _report(vol_id, "GAUSS", 89,
                f"Filtered: {n_before} → {n_after} Gaussians "
                f"(removed {n_before - n_after} outliers/floaters)",
                report_fn)
        del xyz, scales, max_scale, in_bounds, visible, not_needle, keep, log_scales, sorted_log, aniso_ratio

        # --- 5d. Statistical outlier removal (SOR) ---
        from scipy.spatial import cKDTree

        xyz_np = merged_model.positions.detach().cpu().numpy()
        k_sor = 16
        tree = cKDTree(xyz_np)
        dists, idx = tree.query(xyz_np, k=k_sor + 1)  # +1: first is self (dist=0)
        mean_dists = dists[:, 1:].mean(axis=1)
        mu = mean_dists.mean()
        sigma = mean_dists.std()
        sor_thresh = mu + 3.0 * sigma
        sor_keep = torch.tensor(mean_dists <= sor_thresh, device=merged_model._xyz.device)
        n_before_sor = merged_model.num_gaussians
        merged_model.filter_by_mask(sor_keep)
        n_removed_sor = n_before_sor - merged_model.num_gaussians
        if n_removed_sor > 0:
            _report(vol_id, "GAUSS", 89,
                    f"SOR: removed {n_removed_sor} isolated Gaussians "
                    f"(k={k_sor}, threshold={sor_thresh:.4f})",
                    report_fn)
        del xyz_np, tree, dists, idx, mean_dists, sor_keep

        # --- 5e. Connected-component filter ---
        from scipy.sparse import csr_matrix
        from scipy.sparse.csgraph import connected_components

        xyz_np = merged_model.positions.detach().cpu().numpy()
        N_cc = len(xyz_np)
        tree = cKDTree(xyz_np)
        _, idx_k = tree.query(xyz_np, k=k_sor + 1)
        rows = np.repeat(np.arange(N_cc), k_sor)
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

    # Define rendering extent in local coordinates
    render_extent = (
        float(local_pc_min[0] - pad_m), float(local_pc_max[0] + pad_m),
        float(local_pc_min[1] - pad_m), float(local_pc_max[1] + pad_m),
        float(local_pc_min[2] - pad_m), float(local_pc_max[2] + pad_m),
    )

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # --- 6. Render orthophoto ---
    _report(vol_id, "GAUSS", 90, "Rendering orthographic TDOM…", report_fn)
    result = render_orthophoto(
        merged_model, gsd=resolution, extent=render_extent, device=device,
    )

    rgb = result["rgb"]
    height = result["height"]
    x_min, x_max, y_min, y_max = result["extent"]
    H, W = rgb.shape[:2]
    _report(vol_id, "GAUSS", 95,
            f"Orthophoto rendered: {W}x{H} px at GSD={resolution}", report_fn)

    # --- 7. Write GeoTIFF ---
    # Translate the local-coordinate extent back to geographic (UTM) coords.
    # geo_origin is float64 to avoid the precision loss that caused banding.
    geo_x_min = float(np.float64(x_min) + geo_origin[0])
    geo_y_max = float(np.float64(y_max) + geo_origin[1])

    # --- Altitude correction: shift model Z to match mean EXIF altitude if available ---
    if mean_exif_alt is not None:
        z_offset = mean_exif_alt - np.mean(height)
        height = height + z_offset
        _report(vol_id, "GAUSS", 96, f"Shifted height map by {z_offset:.2f} m to match mean EXIF altitude {mean_exif_alt:.2f}", report_fn)
    else:
        _report(vol_id, "GAUSS", 96, "No EXIF altitudes found; using model Z for height map.", report_fn)

    _report(vol_id, "GAUSS", 96, "Writing GeoTIFF…", report_fn)

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
