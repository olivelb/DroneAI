"""
True Orthophoto generation via 2.5D DSM + Voronoi Mosaicking.

Uses PyTorch CUDA tensors for GPU-accelerated orthorectification.
Memory usage is O(1) with respect to image count — images are processed
one at a time and discarded, preventing OOM on 600+ image datasets.
"""
import os
import json
import math
import numpy as np
import rasterio
from rasterio.transform import from_origin
from PIL import Image
import torch
import torch.nn.functional as F
import pycolmap
from plyfile import PlyData

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def report(vol_id, step, progress, msg, report_fn):
    """Unified progress reporting — either via callback or logger."""
    if report_fn:
        report_fn(vol_id, step, progress, log=msg)
    else:
        logger.info("[%s %s%%] %s", step, progress, msg)


def _get_rotation_and_translation(image):
    """
    Extract rotation matrix (3x3 np.ndarray) and translation vector (np.ndarray)
    from a pycolmap Image object, handling API differences across versions.
    """
    # --- Strategy 1: COLMAP 4 modern pycolmap (>= 0.6) ---
    try:
        cfw = image.cam_from_world
        if callable(cfw):
            cfw = cfw()
        rot = cfw.rotation
        if callable(rot):
            rot = rot()
        mat = rot.matrix()
        if callable(mat):
            mat = mat()
        R = np.asarray(mat, dtype=np.float64)
        t = np.asarray(cfw.translation, dtype=np.float64)
        if R.shape == (3, 3):
            return R, t
    except (AttributeError, TypeError):
        pass

    # --- Strategy 2: Legacy pycolmap with rotmat() / tvec ---
    try:
        R = np.asarray(image.rotmat(), dtype=np.float64)
        t = np.asarray(image.tvec, dtype=np.float64)
        if R.shape == (3, 3):
            return R, t
    except AttributeError:
        pass

    # --- Strategy 3: Build from quaternion ---
    try:
        from scipy.spatial.transform import Rotation as ScipyR
        q = image.qvec  # COLMAP order: [w, x, y, z]
        R = ScipyR.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()
        t = np.asarray(image.tvec, dtype=np.float64)
        return R, t
    except Exception as e:
        raise RuntimeError(f"Cannot extract camera pose from pycolmap Image: {e}") from e


# ---------------------------------------------------------------------------
#  DSM Gap-Filling (GPU)
# ---------------------------------------------------------------------------

def fill_dsm_gaps_gpu(dsm_tensor, nodata_value, iterations=3):
    """Fill small holes in the DSM using dilate-then-average on the GPU."""
    mask = (dsm_tensor != nodata_value).float().unsqueeze(0).unsqueeze(0)
    dsm_filled = dsm_tensor.clone().unsqueeze(0).unsqueeze(0)
    dsm_filled[mask == 0] = 0.0

    for _ in range(iterations):
        dilated_mask = F.max_pool2d(mask, kernel_size=3, stride=1, padding=1)
        new_pixels = (dilated_mask > 0) & (mask == 0)

        if not new_pixels.any():
            break

        neighbour_sum = F.avg_pool2d(dsm_filled * mask, kernel_size=3, stride=1, padding=1, divisor_override=1)
        neighbour_count = F.avg_pool2d(mask, kernel_size=3, stride=1, padding=1, divisor_override=1)

        safe_count = neighbour_count.clamp(min=1e-6)
        fill_vals = neighbour_sum / safe_count

        dsm_filled = torch.where(new_pixels, fill_vals, dsm_filled)
        mask = dilated_mask

    return dsm_filled.squeeze(0).squeeze(0)


# ---------------------------------------------------------------------------
#  DSM construction + Voronoi assignment
# ---------------------------------------------------------------------------

NODATA = -10000.0

def build_dsm_and_voronoi(ply_path, reconstruction, transform_data, resolution, device):
    """
    Build a 2.5D DSM and per-pixel Voronoi camera assignment.

    The DSM extent is CLIPPED to the convex hull of camera nadir points
    (with generous padding) to avoid wasting pixels on areas no camera covers.
    """
    # ---- Load PLY ----
    plydata = PlyData.read(ply_path)
    x = np.asarray(plydata['vertex']['x'], dtype=np.float64)
    y = np.asarray(plydata['vertex']['y'], dtype=np.float64)
    z = np.asarray(plydata['vertex']['z'], dtype=np.float64)

    if len(x) == 0:
        raise ValueError("Point cloud is empty.")

    # Extract Sim3 parameters
    R_t = scale = t_vec = None
    if transform_data:
        R_t = np.array(transform_data["R"])
        scale = transform_data["scale"]
        t_vec = np.array(transform_data["t"])

    # Transform point cloud to geo coordinates
    if transform_data:
        xyz = np.column_stack([x, y, z])
        xyz_geo = (scale * (R_t @ xyz.T) + t_vec[:, np.newaxis]).T
        x, y, z = xyz_geo[:, 0], xyz_geo[:, 1], xyz_geo[:, 2]

    import math
    # ---- Filter non-nadir cameras ----
    valid_images = []
    for img_id, image in reconstruction.images.items():
        if transform_data:
            R_cw, _ = _get_rotation_and_translation(image)
            v_local = R_cw[2, :]
            v_geo = R_t @ v_local
            v_geo = v_geo / np.linalg.norm(v_geo)
            dot = max(-1.0, min(1.0, -v_geo[2]))
            angle = math.degrees(math.acos(dot))
            if angle <= 25.0:
                valid_images.append(img_id)
        else:
            valid_images.append(img_id)
            
    if not valid_images:
        valid_images = list(reconstruction.images.keys())

    # ---- Compute camera nadir positions for DSM clipping ----
    cam_xs = []
    cam_ys = []
    for img_id in valid_images:
        image = reconstruction.images[img_id]
        cam_center_local = np.asarray(image.projection_center(), dtype=np.float64)
        if transform_data:
            c_geo = scale * (R_t @ cam_center_local) + t_vec
        else:
            c_geo = cam_center_local
        cam_xs.append(c_geo[0])
        cam_ys.append(c_geo[1])

    cam_xs = np.array(cam_xs)
    cam_ys = np.array(cam_ys)

    # Clip DSM extent: use camera extent + generous padding (50% of span)
    # This avoids putting pixels where no camera can see them.
    cam_span_x = cam_xs.max() - cam_xs.min()
    cam_span_y = cam_ys.max() - cam_ys.min()
    pad_x = max(cam_span_x * 0.15, 20.0)  # at least 20m padding
    pad_y = max(cam_span_y * 0.15, 20.0)

    # Clip point cloud extent to camera coverage + padding
    clip_min_x = cam_xs.min() - pad_x
    clip_max_x = cam_xs.max() + pad_x
    clip_min_y = cam_ys.min() - pad_y
    clip_max_y = cam_ys.max() + pad_y

    # Also intersect with actual point cloud extent
    min_x = max(clip_min_x, float(np.min(x)))
    max_x = min(clip_max_x, float(np.max(x)))
    min_y = max(clip_min_y, float(np.min(y)))
    max_y = min(clip_max_y, float(np.max(y)))

    width_m = max_x - min_x
    height_m = max_y - min_y
    width = max(1, int(np.ceil(width_m / resolution)))
    height = max(1, int(np.ceil(height_m / resolution)))

    # ---- Filter points to clipped extent ----
    in_extent = (x >= min_x) & (x <= max_x) & (y >= min_y) & (y <= max_y)
    x_c = x[in_extent]
    y_c = y[in_extent]
    z_c = z[in_extent]

    # ---- Scatter-max to build DSM ----
    col = torch.from_numpy(((x_c - min_x) / resolution).astype(np.int64)).clamp(0, width - 1).to(device)
    row = torch.from_numpy(((max_y - y_c) / resolution).astype(np.int64)).clamp(0, height - 1).to(device)
    z_t = torch.from_numpy(z_c.astype(np.float32)).to(device)

    flat_idx = row * width + col
    dsm_flat = torch.full((height * width,), NODATA, dtype=torch.float32, device=device)
    dsm_flat.scatter_reduce_(0, flat_idx, z_t, reduce="amax", include_self=False)

    dsm = dsm_flat.view(height, width)

    # Track which pixels had ACTUAL point cloud data BEFORE gap filling
    raw_valid = dsm > NODATA

    # Gap-fill for small holes only
    dsm = fill_dsm_gaps_gpu(dsm, NODATA, iterations=5)

    # ---- Voronoi mask ----
    min_distance_sq = torch.full((height, width), float('inf'), dtype=torch.float64, device=device)
    voronoi_map = torch.full((height, width), -1, dtype=torch.long, device=device)

    grid_row, grid_col = torch.meshgrid(
        torch.arange(height, device=device, dtype=torch.float64),
        torch.arange(width, device=device, dtype=torch.float64),
        indexing='ij',
    )
    world_gx = grid_col * resolution + min_x
    world_gy = max_y - grid_row * resolution

    # Valid mask = gap-filled DSM pixels (use gap-filled for smooth coverage)
    valid_mask = dsm > NODATA

    for img_id in valid_images:
        image = reconstruction.images[img_id]
        cam_center_local = np.asarray(image.projection_center(), dtype=np.float64)

        if transform_data:
            c_geo = scale * (R_t @ cam_center_local) + t_vec
        else:
            c_geo = cam_center_local

        cx, cy = float(c_geo[0]), float(c_geo[1])

        dist_sq = (world_gx - cx) ** 2 + (world_gy - cy) ** 2
        dist_sq = torch.where(valid_mask, dist_sq, torch.tensor(float('inf'), device=device))

        closer = dist_sq < min_distance_sq
        min_distance_sq[closer] = dist_sq[closer]
        voronoi_map[closer] = img_id

    return dsm, voronoi_map, valid_mask, min_x, max_y, width, height, set(valid_images)


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------

def generate_true_orthophoto_pytorch(dense_path, ortho_file, utm_crs, vol_id,
                                     transform_file=None, report_fn=None, resolution=0.05):
    """
    Generate a True Orthophoto via 2.5D DSM ray-casting on the GPU.

    Each source image is warped through the DSM one at a time (O(1) VRAM),
    and each pixel is assigned to the camera whose nadir is closest (Voronoi).
    Pixels that cannot be projected through their Voronoi winner are painted
    from the nearest camera that CAN see them (multi-pass fallback).
    The final mosaic is written as a compressed GeoTIFF.
    """
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    report(vol_id, "ORTHO", 95, f"Starting PyTorch True Orthophoto on {device}…", report_fn)

    ply_path = os.path.join(dense_path, "fused.ply")
    if not os.path.exists(ply_path):
        raise FileNotFoundError(f"Missing point cloud: {ply_path}")

    transform_data = None
    if transform_file and os.path.exists(transform_file):
        with open(transform_file, 'r') as tf:
            transform_data = json.load(tf)


    report(vol_id, "ORTHO", 96, "Building 2.5D DSM and Voronoi map…", report_fn)
    reconstruction = pycolmap.Reconstruction(os.path.join(dense_path, "sparse"))

    dsm, voronoi_map, valid_dsm_mask, min_x, max_y, width, height, valid_images = build_dsm_and_voronoi(
        ply_path, reconstruction, transform_data, resolution, device,
    )

    # Output buffer (initialized to 0 = black)
    ortho_rgb = torch.zeros((3, height, width), dtype=torch.uint8, device=device)
    # Track which pixels have been successfully painted
    painted = torch.zeros((height, width), dtype=torch.bool, device=device)

    # World coordinate grids
    grid_row, grid_col = torch.meshgrid(
        torch.arange(height, device=device, dtype=torch.float64),
        torch.arange(width, device=device, dtype=torch.float64),
        indexing='ij',
    )
    world_x = grid_col * resolution + min_x
    world_y = max_y - grid_row * resolution
    world_z = dsm

    # Inverse Sim3 to go from geographic back to COLMAP local coords
    if transform_data:
        R_np = np.array(transform_data["R"], dtype=np.float64)
        scale_val = float(transform_data["scale"])
        t_np = np.array(transform_data["t"], dtype=np.float64)
        R_inv = np.linalg.inv(R_np)

        R_inv_t = torch.tensor(R_inv, device=device, dtype=torch.float64)
        t_t = torch.tensor(t_np, device=device, dtype=torch.float64)

        def geo_to_local(x, y, z):
            """(N,) tensors → (N, 3) float32 local coords."""
            pts = torch.stack([x.double(), y.double(), z.double()], dim=-1)
            pts = (pts - t_t) / scale_val
            return (R_inv_t @ pts.T).T.float()
    else:
        def geo_to_local(x, y, z):
            return torch.stack([x, y, z], dim=-1).float()

    images_dir = os.path.join(dense_path, "images")
    total_images = len(reconstruction.images)

    def _warp_image(img_id, image, pixel_mask):
        """Warp one image onto the DSM for pixels in pixel_mask.
        Returns the number of successfully painted pixels."""
        n_pixels = int(pixel_mask.sum())
        if n_pixels == 0:
            return 0

        pts_local = geo_to_local(world_x[pixel_mask], world_y[pixel_mask], world_z[pixel_mask])

        camera = reconstruction.cameras[image.camera_id]
        R_cw_np, t_cw_np = _get_rotation_and_translation(image)
        R_cw = torch.tensor(R_cw_np, dtype=torch.float32, device=device)
        t_cw = torch.tensor(t_cw_np, dtype=torch.float32, device=device)

        pts_cam = (R_cw @ pts_local.T).T + t_cw

        front = pts_cam[:, 2] > 0
        if not front.any():
            return 0

        pts_cam_f = pts_cam[front]

        # Pinhole projection
        model = camera.model_name if hasattr(camera, 'model_name') else str(camera.model)
        if model in ("PINHOLE", "SIMPLE_PINHOLE", "0", "1"):
            fx = float(camera.params[0])
            if model in ("PINHOLE", "1"):
                fy = float(camera.params[1])
                cx = float(camera.params[2])
                cy = float(camera.params[3])
            else:
                fy = fx
                cx = float(camera.params[1])
                cy = float(camera.params[2])

            u = (pts_cam_f[:, 0] / pts_cam_f[:, 2]) * fx + cx
            v = (pts_cam_f[:, 1] / pts_cam_f[:, 2]) * fy + cy
        else:
            pts_cam_cpu = pts_cam_f.cpu().numpy()
            uv_cpu = np.array([camera.img_from_cam(p) for p in pts_cam_cpu])
            u = torch.tensor(uv_cpu[:, 0], device=device, dtype=torch.float64)
            v = torch.tensor(uv_cpu[:, 1], device=device, dtype=torch.float64)

        w_img = int(camera.width)
        h_img = int(camera.height)

        # Use a small margin (2px) inside the image borders to avoid edge artifacts
        margin = 2
        in_bounds = (u >= margin) & (u < w_img - margin) & (v >= margin) & (v < h_img - margin)
        if not in_bounds.any():
            return 0

        u_valid = u[in_bounds]
        v_valid = v[in_bounds]

        # Load source image
        img_path = os.path.join(images_dir, image.name)
        if not os.path.exists(img_path):
            return 0

        with Image.open(img_path) as pil_img:
            img_np = np.asarray(pil_img)
            if img_np.ndim == 2:
                img_np = np.stack([img_np] * 3, axis=-1)
            elif img_np.shape[2] == 4:
                img_np = img_np[:, :, :3]
            tensor_img = torch.from_numpy(img_np.copy()).permute(2, 0, 1).to(device).float()

        # Bilinear sample
        u_norm = (u_valid / (w_img - 1)) * 2.0 - 1.0
        v_norm = (v_valid / (h_img - 1)) * 2.0 - 1.0
        grid = torch.stack([u_norm, v_norm], dim=-1).view(1, 1, -1, 2)

        sampled = F.grid_sample(
            tensor_img.unsqueeze(0), grid, mode='bilinear',
            padding_mode='zeros', align_corners=True,
        )
        colors = sampled.squeeze(0).squeeze(1)  # (3, N)

        # Write to output buffer
        mask_indices = pixel_mask.nonzero(as_tuple=False)     # (M, 2)
        valid_indices = mask_indices[front][in_bounds]         # (K, 2) row, col

        ortho_rgb[:, valid_indices[:, 0], valid_indices[:, 1]] = colors.clamp(0, 255).to(torch.uint8)
        painted[valid_indices[:, 0], valid_indices[:, 1]] = True

        # Free VRAM
        del tensor_img, grid, sampled, colors, pts_local, pts_cam
        if device.type == 'cuda':
            torch.cuda.empty_cache()

        return int(valid_indices.shape[0])

    # ---- Pass 1: Voronoi-optimal assignment ----
    report(vol_id, "ORTHO", 97, f"Pass 1: Voronoi warping {total_images} images…", report_fn)
    images_processed = 0
    total_painted = 0

    for img_id, image in reconstruction.images.items():
        mask = (voronoi_map == img_id) & valid_dsm_mask
        n_painted = _warp_image(img_id, image, mask)
        total_painted += n_painted

        images_processed += 1
        if images_processed % 50 == 0 or images_processed == total_images:
            pct = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
            report(vol_id, "ORTHO", 97,
                   f"Pass 1: {images_processed}/{total_images} images, {pct:.1f}% filled…", report_fn)

    pass1_fill = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
    report(vol_id, "ORTHO", 97, f"Pass 1 done: {pass1_fill:.1f}% filled.", report_fn)

    # ---- Pass 2: Fill remaining unpainted pixels from ANY visible camera ----
    # For each unpainted pixel, try all cameras sorted by distance, use the first
    # one that can see it. This is done image-by-image for memory efficiency.
    unpainted = valid_dsm_mask & (~painted)
    n_unpainted = int(unpainted.sum())

    if n_unpainted > 0:
        report(vol_id, "ORTHO", 97, f"Pass 2: Filling {n_unpainted} remaining pixels…", report_fn)

        # Sort images by distance to center of unpainted region for efficiency
        if n_unpainted > 0:
            unpainted_coords = unpainted.nonzero(as_tuple=False).float()
            center_row = unpainted_coords[:, 0].mean()
            center_col = unpainted_coords[:, 1].mean()
            center_x = float(center_col * resolution + min_x)
            center_y = float(max_y - center_row * resolution)

            img_dists = []
            for img_id in valid_images:
                image = reconstruction.images[img_id]
                c_local = np.asarray(image.projection_center(), dtype=np.float64)
                if transform_data:
                    c_geo = float(transform_data["scale"]) * (np.array(transform_data["R"]) @ c_local) + np.array(transform_data["t"])
                else:
                    c_geo = c_local
                d = (c_geo[0] - center_x)**2 + (c_geo[1] - center_y)**2
                img_dists.append((d, img_id, image))
            img_dists.sort()

            pass2_processed = 0
            for _, img_id, image in img_dists:
                still_unpainted = valid_dsm_mask & (~painted)
                if not still_unpainted.any():
                    break
                n = _warp_image(img_id, image, still_unpainted)
                pass2_processed += 1
                if pass2_processed % 50 == 0:
                    remaining = int((valid_dsm_mask & (~painted)).sum())
                    report(vol_id, "ORTHO", 97,
                           f"Pass 2: tried {pass2_processed}/{total_images}, {remaining} pixels left…", report_fn)

    final_fill = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
    report(vol_id, "ORTHO", 98, f"Mosaicking complete: {final_fill:.1f}% fill rate.", report_fn)

    # ---- Write GeoTIFF ----
    report(vol_id, "ORTHO", 98, "Writing GeoTIFF…", report_fn)
    final_rgb = ortho_rgb.cpu().numpy()

    geo_transform = from_origin(min_x, max_y, resolution, resolution)
    crs_to_use = utm_crs if utm_crs else 'EPSG:4326'

    with rasterio.open(
        ortho_file, 'w', driver='GTiff',
        height=height, width=width, count=3,
        dtype='uint8', crs=crs_to_use, transform=geo_transform,
        compress='lzw',
    ) as dst:
        dst.write(final_rgb)

    report(vol_id, "ORTHO", 99, f"True Orthophoto written: {width}×{height} px @ {resolution} m/px ({final_fill:.1f}% coverage)", report_fn)
