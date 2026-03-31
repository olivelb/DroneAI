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
from ortho_edge_support import build_edge_assignment_paint_mask, build_mixed_depth_rejection_mask

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


def _read_colmap_dense_array(path):
    """Read a COLMAP dense array (*.bin) with the width&height&channels& header."""
    header_tokens = []
    current_token = []

    with open(path, "rb") as handle:
        while len(header_tokens) < 3:
            char = handle.read(1)
            if not char:
                raise ValueError(f"Incomplete COLMAP dense map header: {path}")
            if char == b"&":
                header_tokens.append(b"".join(current_token).decode("ascii"))
                current_token = []
            else:
                current_token.append(char)

        width, height, channels = (int(token) for token in header_tokens)
        payload = handle.read()

    data = np.frombuffer(payload, dtype="<f4")
    expected_values = width * height * channels
    if data.size < expected_values:
        raise ValueError(
            f"COLMAP dense map payload is truncated for {path}: expected {expected_values} float32 values, got {data.size}"
        )
    if data.size > expected_values:
        data = data[:expected_values]

    array = data.reshape((height, width, channels))
    if channels == 1:
        return array[:, :, 0]
    return array


def _resolve_dense_map_path(dense_path, image_name, folder_name, preferred_type):
    folder_path = os.path.join(dense_path, "stereo", folder_name)
    preferred_path = os.path.join(folder_path, f"{image_name}.{preferred_type}.bin")
    if os.path.exists(preferred_path):
        return preferred_path, preferred_type

    fallback_type = "photometric" if preferred_type == "geometric" else "geometric"
    fallback_path = os.path.join(folder_path, f"{image_name}.{fallback_type}.bin")
    if os.path.exists(fallback_path):
        return fallback_path, fallback_type

    return None, None


# ---------------------------------------------------------------------------
#  DSM Gap-Filling (GPU)
# ---------------------------------------------------------------------------

def fill_dsm_gaps_gpu(dsm_tensor, nodata_value, iterations=3):
    """Fill small DSM holes and track Chebyshev distance from raw support."""
    mask = (dsm_tensor != nodata_value).float().unsqueeze(0).unsqueeze(0)
    dsm_filled = dsm_tensor.clone().unsqueeze(0).unsqueeze(0)
    dsm_filled[mask == 0] = 0.0
    support_distance = torch.full_like(dsm_tensor, float(iterations + 1), dtype=torch.float32)
    support_distance[dsm_tensor != nodata_value] = 0.0

    for iteration_index in range(iterations):
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
        support_distance = torch.where(
            new_pixels.squeeze(0).squeeze(0),
            torch.full_like(support_distance, float(iteration_index + 1)),
            support_distance,
        )

    return dsm_filled.squeeze(0).squeeze(0), support_distance


def estimate_surface_normals_gpu(dsm_tensor, resolution):
    """Estimate upward-facing surface normals from the gap-filled DSM."""
    smoothed = F.avg_pool2d(
        dsm_tensor.unsqueeze(0).unsqueeze(0),
        kernel_size=3,
        stride=1,
        padding=1,
    ).squeeze(0).squeeze(0)

    dz_dx = torch.zeros_like(smoothed)
    dz_dy = torch.zeros_like(smoothed)

    dz_dx[:, 1:-1] = (smoothed[:, 2:] - smoothed[:, :-2]) / (2.0 * resolution)
    dz_dx[:, 0] = (smoothed[:, 1] - smoothed[:, 0]) / resolution
    dz_dx[:, -1] = (smoothed[:, -1] - smoothed[:, -2]) / resolution

    # Raster rows increase downward while world Y decreases downward.
    dz_dy[1:-1, :] = (smoothed[:-2, :] - smoothed[2:, :]) / (2.0 * resolution)
    dz_dy[0, :] = (smoothed[0, :] - smoothed[1, :]) / resolution
    dz_dy[-1, :] = (smoothed[-2, :] - smoothed[-1, :]) / resolution

    normals = torch.stack([
        -dz_dx,
        -dz_dy,
        torch.ones_like(smoothed),
    ], dim=-1)
    return F.normalize(normals, dim=-1, eps=1e-8)


def fill_small_color_holes_gpu(rgb_tensor, painted_mask, valid_mask, iterations=6, min_neighbors=3):
    """Fill small remaining holes from neighboring painted colors on the GPU."""
    rgb = rgb_tensor.float().unsqueeze(0)
    painted = painted_mask.float().unsqueeze(0).unsqueeze(0)
    allowed = valid_mask.unsqueeze(0).unsqueeze(0)

    for _ in range(iterations):
        expanded = F.max_pool2d(painted, kernel_size=3, stride=1, padding=1)
        neighbor_count = F.avg_pool2d(painted, kernel_size=3, stride=1, padding=1, divisor_override=1)
        new_pixels = (expanded > 0) & (painted == 0) & (allowed > 0) & (neighbor_count >= float(min_neighbors))
        if not new_pixels.any():
            break

        safe_count = neighbor_count.clamp(min=1.0)
        for channel_index in range(3):
            channel = rgb[:, channel_index:channel_index + 1, :, :]
            neighbor_sum = F.avg_pool2d(channel * painted, kernel_size=3, stride=1, padding=1, divisor_override=1)
            fill_values = neighbor_sum / safe_count
            channel = torch.where(new_pixels, fill_values, channel)
            rgb[:, channel_index:channel_index + 1, :, :] = channel

        painted = torch.where(new_pixels, torch.ones_like(painted), painted)

    return rgb.squeeze(0).clamp(0, 255).to(torch.uint8), painted.squeeze(0).squeeze(0).bool()
def sample_source_depth_visibility(u, v, cam_depth, depth_map, camera_width, camera_height, device, tolerance_m, min_depth, neighborhood_radius_px=1):
    """Check whether projected points are visible in the source depth map."""
    if u.numel() == 0:
        return torch.empty((0,), dtype=torch.bool, device=device)

    u = u.to(dtype=torch.float32)
    v = v.to(dtype=torch.float32)
    cam_depth = cam_depth.to(dtype=torch.float32)

    depth_height, depth_width = depth_map.shape
    depth_tensor = torch.from_numpy(depth_map.copy()).to(device=device, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    valid_depth = torch.isfinite(depth_tensor) & (depth_tensor > float(min_depth))
    invalid_fill_depth = torch.full_like(depth_tensor, 1.0e9)
    conservative_depth_tensor = torch.where(valid_depth, depth_tensor, invalid_fill_depth)
    if neighborhood_radius_px > 0:
        kernel_size = neighborhood_radius_px * 2 + 1
        conservative_depth_tensor = -F.max_pool2d(
            -conservative_depth_tensor,
            kernel_size=kernel_size,
            stride=1,
            padding=neighborhood_radius_px,
        )

    scale_x = float(camera_width) / float(depth_width)
    scale_y = float(camera_height) / float(depth_height)

    depth_u = ((u + 0.5) / scale_x) - 0.5
    depth_v = ((v + 0.5) / scale_y) - 0.5

    if depth_width > 1:
        u_norm = (depth_u / float(depth_width - 1)) * 2.0 - 1.0
    else:
        u_norm = torch.zeros_like(depth_u)
    if depth_height > 1:
        v_norm = (depth_v / float(depth_height - 1)) * 2.0 - 1.0
    else:
        v_norm = torch.zeros_like(depth_v)

    grid = torch.stack([u_norm, v_norm], dim=-1).to(dtype=depth_tensor.dtype).view(1, 1, -1, 2)
    sampled_depth = F.grid_sample(
        conservative_depth_tensor,
        grid,
        mode='nearest',
        padding_mode='zeros',
        align_corners=True,
    ).view(-1)

    valid_sampled_depth = torch.isfinite(sampled_depth) & (sampled_depth < 1.0e8)
    # Only reject when the depth map explicitly shows a CLOSER surface.
    # If the depth map has no data at that pixel (textureless, shadow, etc.),
    # we accept the projection — absence of evidence is not evidence of occlusion.
    explicitly_occluded = valid_sampled_depth & (cam_depth > (sampled_depth + float(tolerance_m)))
    return ~explicitly_occluded


def sample_source_depth_edge_mask(u, v, depth_map, camera_width, camera_height, device, gradient_threshold=0.15):
    """Detect depth discontinuities in the source image at projected locations.

    Returns a boolean mask (True = safe, False = on a depth edge).
    Bilinear sampling near depth edges bleeds foreground/background textures,
    producing white or ghosted pixels.  Rejecting these projections and falling
    back to another camera eliminates the artifact.
    """
    if u.numel() == 0:
        return torch.empty((0,), dtype=torch.bool, device=device)

    u = u.to(dtype=torch.float32)
    v = v.to(dtype=torch.float32)

    depth_height, depth_width = depth_map.shape
    depth_tensor = torch.from_numpy(depth_map.copy()).to(device=device, dtype=torch.float32).unsqueeze(0).unsqueeze(0)
    # Replace invalid depth with 0 so gradients at invalid borders are large
    valid_d = torch.isfinite(depth_tensor) & (depth_tensor > 0.05)
    depth_clean = torch.where(valid_d, depth_tensor, torch.zeros_like(depth_tensor))

    # Compute Sobel-like depth gradient magnitude (normalised by depth)
    # Using a simple 3x3 finite difference
    pad_d = F.pad(depth_clean, (1, 1, 1, 1), mode='replicate')
    grad_x = (pad_d[:, :, 1:-1, 2:] - pad_d[:, :, 1:-1, :-2]) / 2.0
    grad_y = (pad_d[:, :, 2:, 1:-1] - pad_d[:, :, :-2, 1:-1]) / 2.0
    grad_mag = torch.sqrt(grad_x ** 2 + grad_y ** 2).squeeze(0).squeeze(0)
    # Normalise by the depth itself to get relative gradient
    depth_sq = depth_clean.squeeze(0).squeeze(0).clamp(min=0.1)
    rel_grad = grad_mag / depth_sq

    # Sample the relative gradient at projected pixel locations
    scale_x = float(camera_width) / float(depth_width)
    scale_y = float(camera_height) / float(depth_height)
    depth_u = ((u + 0.5) / scale_x) - 0.5
    depth_v = ((v + 0.5) / scale_y) - 0.5

    if depth_width > 1:
        u_norm = (depth_u / float(depth_width - 1)) * 2.0 - 1.0
    else:
        u_norm = torch.zeros_like(depth_u)
    if depth_height > 1:
        v_norm = (depth_v / float(depth_height - 1)) * 2.0 - 1.0
    else:
        v_norm = torch.zeros_like(depth_v)

    grid = torch.stack([u_norm, v_norm], dim=-1).to(dtype=rel_grad.dtype).view(1, 1, -1, 2)
    sampled_grad = F.grid_sample(
        rel_grad.unsqueeze(0).unsqueeze(0),
        grid,
        mode='nearest',
        padding_mode='border',
        align_corners=True,
    ).view(-1)

    return sampled_grad < float(gradient_threshold)


def select_layer_from_depth(u, v, cam_depth_high, cam_depth_low, depth_map,
                           camera_width, camera_height, device, tolerance_m,
                           min_depth):
    """For dual-layer DSM pixels, decide which layer each camera actually sees.

    Returns a boolean tensor (True = high layer visible, False = low layer).
    The decision is based on comparing both projected camera-space depths
    against the source depth map.  If the source depth agrees with the high
    layer (foreground / object), that layer wins.  Otherwise the low layer
    (ground) wins, which lets the ground texture show through instead of
    producing a halo.
    """
    if u.numel() == 0:
        return torch.empty((0,), dtype=torch.bool, device=device)

    u = u.to(dtype=torch.float32)
    v = v.to(dtype=torch.float32)
    cam_depth_high = cam_depth_high.to(dtype=torch.float32)
    cam_depth_low = cam_depth_low.to(dtype=torch.float32)

    depth_height, depth_width = depth_map.shape
    depth_tensor = (torch.from_numpy(depth_map.copy())
                    .to(device=device, dtype=torch.float32)
                    .unsqueeze(0).unsqueeze(0))
    valid_depth = torch.isfinite(depth_tensor) & (depth_tensor > float(min_depth))
    invalid_fill = torch.full_like(depth_tensor, 1.0e9)
    depth_tensor = torch.where(valid_depth, depth_tensor, invalid_fill)

    scale_x = float(camera_width) / float(depth_width)
    scale_y = float(camera_height) / float(depth_height)
    depth_u = ((u + 0.5) / scale_x) - 0.5
    depth_v = ((v + 0.5) / scale_y) - 0.5
    if depth_width > 1:
        u_norm = (depth_u / float(depth_width - 1)) * 2.0 - 1.0
    else:
        u_norm = torch.zeros_like(depth_u)
    if depth_height > 1:
        v_norm = (depth_v / float(depth_height - 1)) * 2.0 - 1.0
    else:
        v_norm = torch.zeros_like(depth_v)

    grid = (torch.stack([u_norm, v_norm], dim=-1)
            .to(dtype=depth_tensor.dtype).view(1, 1, -1, 2))
    sampled_depth = F.grid_sample(
        depth_tensor, grid, mode='nearest',
        padding_mode='zeros', align_corners=True,
    ).view(-1)

    valid_sampled = torch.isfinite(sampled_depth) & (sampled_depth < 1.0e8)
    tol = float(tolerance_m)

    # Camera sees the HIGH (foreground) surface when the source depth map
    # agrees with cam_depth_high (within tolerance).  Otherwise prefer LOW.
    high_agrees = valid_sampled & (torch.abs(cam_depth_high - sampled_depth) <= tol)
    low_agrees = valid_sampled & (torch.abs(cam_depth_low - sampled_depth) <= tol)

    # When both agree (rare, surfaces very close) or neither agrees, fall back
    # to whichever depth is closer to the source depth map.
    both_or_neither = (high_agrees == low_agrees)
    high_closer = torch.abs(cam_depth_high - sampled_depth) <= torch.abs(cam_depth_low - sampled_depth)
    use_high = torch.where(both_or_neither, high_closer, high_agrees)

    # If no valid depth data, default to high (object) layer.
    use_high = torch.where(valid_sampled, use_high, torch.ones_like(use_high))
    return use_high


# ---------------------------------------------------------------------------
#  DSM construction + Voronoi assignment
# ---------------------------------------------------------------------------

NODATA = -10000.0

def build_dsm_and_voronoi(dense_path, reconstruction, transform_data, resolution, device):
    """
    Build a 2.5D DSM from COLMAP depth maps and a per-pixel camera assignment.

    The DSM extent is clipped to the camera footprint with padding to avoid
    wasting pixels on areas no camera covers. Instead of fusing a sparse point
    cloud first, this path projects PatchMatch depth samples directly into the
    ortho grid and then scores source cameras per DSM cell.
    """
    # Extract Sim3 parameters
    R_t = scale = t_vec = None
    if transform_data:
        R_t = np.array(transform_data["R"], dtype=np.float64)
        scale = float(transform_data["scale"])
        t_vec = np.array(transform_data["t"], dtype=np.float64)

    import math
    # ---- Filter cameras for primary and fallback passes ----
    # Compute the "down" direction in COLMAP local coords from the Sim3
    # alignment: geo-down [0,0,-1] mapped back to local via R_t^T.
    # This avoids the Sim3 rotation inflating nadir angles.
    NADIR_THRESHOLD_DEG = float(os.getenv("ORTHO_DSM_NADIR_THRESHOLD_DEG", "20.0"))
    FALLBACK_NADIR_THRESHOLD_DEG = float(os.getenv("ORTHO_DSM_FALLBACK_NADIR_THRESHOLD_DEG", "65.0"))
    if transform_data:
        down_local = R_t.T @ np.array([0.0, 0.0, -1.0])
        down_local = down_local / np.linalg.norm(down_local)
    else:
        down_local = None

    valid_images = []
    fallback_images = []
    image_angles_deg = {}
    for img_id, image in reconstruction.images.items():
        if down_local is not None:
            R_cw, _ = _get_rotation_and_translation(image)
            v_cam = R_cw[2, :]  # camera viewing direction in COLMAP world
            v_cam = v_cam / np.linalg.norm(v_cam)
            dot = max(-1.0, min(1.0, float(np.dot(v_cam, down_local))))
            angle = math.degrees(math.acos(dot))
            image_angles_deg[img_id] = angle
            if angle <= NADIR_THRESHOLD_DEG:
                valid_images.append(img_id)
            if angle <= FALLBACK_NADIR_THRESHOLD_DEG:
                fallback_images.append(img_id)
        else:
            image_angles_deg[img_id] = None
            valid_images.append(img_id)
            fallback_images.append(img_id)

    if not valid_images:
        valid_images = list(reconstruction.images.keys())
    if not fallback_images:
        fallback_images = list(reconstruction.images.keys())

    dsm_build_max_view_angle_deg = float(
        os.getenv("ORTHO_DSM_BUILD_MAX_VIEW_ANGLE_DEG", str(NADIR_THRESHOLD_DEG))
    )
    dsm_source_image_ids = []
    for img_id, image in reconstruction.images.items():
        image_angle = image_angles_deg.get(img_id)
        if image_angle is None or image_angle <= dsm_build_max_view_angle_deg:
            dsm_source_image_ids.append(img_id)

    if not dsm_source_image_ids:
        if valid_images:
            dsm_source_image_ids = list(valid_images)
        elif fallback_images:
            dsm_source_image_ids = list(fallback_images)
        else:
            dsm_source_image_ids = list(reconstruction.images.keys())

    # ---- Compute camera nadir positions for DSM clipping ----
    cam_xs = []
    cam_ys = []
    for img_id in dsm_source_image_ids:
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

    # Clip DSM extent to camera coverage + padding.
    clip_min_x = cam_xs.min() - pad_x
    clip_max_x = cam_xs.max() + pad_x
    clip_min_y = cam_ys.min() - pad_y
    clip_max_y = cam_ys.max() + pad_y

    min_x = clip_min_x
    max_x = clip_max_x
    min_y = clip_min_y
    max_y = clip_max_y

    width_m = max_x - min_x
    height_m = max_y - min_y
    width = max(1, int(np.ceil(width_m / resolution)))
    height = max(1, int(np.ceil(height_m / resolution)))

    dsm_flat = torch.full((height * width,), NODATA, dtype=torch.float32, device=device)
    dsm_min_flat = torch.full((height * width,), float("inf"), dtype=torch.float32, device=device)
    sample_count_flat = torch.zeros((height * width,), dtype=torch.float32, device=device)

    depth_map_type = str(os.getenv("ORTHO_DSM_DEPTH_MAP_TYPE", "geometric")).strip().lower() or "geometric"
    depth_stride = max(1, int(os.getenv("ORTHO_DSM_DEPTH_STRIDE", "1")))
    row_block_size = max(depth_stride, int(os.getenv("ORTHO_DSM_DEPTH_ROW_BLOCK_SIZE", "128")))
    min_depth = float(os.getenv("ORTHO_DSM_MIN_DEPTH", "0.05"))
    min_normal_norm = float(os.getenv("ORTHO_DSM_MIN_NORMAL_NORM", "0.05"))

    depth_maps_found = 0
    depth_maps_used = 0
    normal_maps_used = 0
    depth_samples_considered = 0
    depth_samples_accepted = 0

    for img_id in dsm_source_image_ids:
        image = reconstruction.images[img_id]
        depth_map_path, resolved_depth_type = _resolve_dense_map_path(
            dense_path,
            image.name,
            "depth_maps",
            depth_map_type,
        )
        if depth_map_path is None:
            continue

        depth_maps_found += 1
        depth_map = _read_colmap_dense_array(depth_map_path)
        if depth_map.ndim != 2:
            raise ValueError(f"Expected a single-channel depth map for {depth_map_path}, got shape {depth_map.shape}")

        normal_map_path, _ = _resolve_dense_map_path(
            dense_path,
            image.name,
            "normal_maps",
            resolved_depth_type,
        )
        normal_map = None
        if normal_map_path is not None:
            normal_map = _read_colmap_dense_array(normal_map_path)
            if normal_map.ndim == 3 and normal_map.shape[2] == 3:
                normal_maps_used += 1
            else:
                normal_map = None

        camera = reconstruction.cameras[image.camera_id]
        model = camera.model_name if hasattr(camera, "model_name") else str(camera.model)
        if model not in ("PINHOLE", "SIMPLE_PINHOLE", "0", "1"):
            logger.warning("Skipping depth-map accumulation for %s: unsupported camera model %s", image.name, model)
            continue

        fx = float(camera.params[0])
        if model in ("PINHOLE", "1"):
            fy = float(camera.params[1])
            px = float(camera.params[2])
            py = float(camera.params[3])
        else:
            fy = fx
            px = float(camera.params[1])
            py = float(camera.params[2])

        depth_height, depth_width = depth_map.shape
        scale_x = float(camera.width) / float(depth_width)
        scale_y = float(camera.height) / float(depth_height)
        col_coords = np.arange(0, depth_width, depth_stride, dtype=np.float64)
        R_cw, t_cw = _get_rotation_and_translation(image)
        t_cw = np.asarray(t_cw, dtype=np.float64)

        image_used = False
        for row_start in range(0, depth_height, row_block_size):
            row_stop = min(depth_height, row_start + row_block_size)
            row_coords = np.arange(row_start, row_stop, depth_stride, dtype=np.float64)
            depth_block = depth_map[row_start:row_stop:depth_stride, ::depth_stride]
            valid = np.isfinite(depth_block) & (depth_block > min_depth)
            if normal_map is not None:
                normal_block = normal_map[row_start:row_stop:depth_stride, ::depth_stride, :]
                valid &= np.isfinite(normal_block).all(axis=2)
                valid &= np.sum(normal_block * normal_block, axis=2) >= (min_normal_norm * min_normal_norm)

            block_pixels = int(depth_block.size)
            depth_samples_considered += block_pixels
            if not np.any(valid):
                continue

            u_idx = np.broadcast_to(col_coords[np.newaxis, :], depth_block.shape)
            v_idx = np.broadcast_to(row_coords[:, np.newaxis], depth_block.shape)

            u = (u_idx[valid] + 0.5) * scale_x - 0.5
            v = (v_idx[valid] + 0.5) * scale_y - 0.5
            depth_values = depth_block[valid].astype(np.float64, copy=False)

            cam_x = ((u - px) / fx) * depth_values
            cam_y = ((v - py) / fy) * depth_values
            cam_points = np.column_stack([cam_x, cam_y, depth_values])
            world_local = (cam_points - t_cw[np.newaxis, :]) @ R_cw

            if transform_data:
                world_points = (scale * (R_t @ world_local.T) + t_vec[:, np.newaxis]).T
            else:
                world_points = world_local

            x_geo = world_points[:, 0]
            y_geo = world_points[:, 1]
            z_geo = world_points[:, 2]
            in_extent = (
                (x_geo >= min_x)
                & (x_geo <= max_x)
                & (y_geo >= min_y)
                & (y_geo <= max_y)
                & np.isfinite(z_geo)
            )
            if not np.any(in_extent):
                continue

            x_geo = x_geo[in_extent]
            y_geo = y_geo[in_extent]
            z_geo = z_geo[in_extent]

            col = np.clip(((x_geo - min_x) / resolution).astype(np.int64), 0, width - 1)
            row = np.clip(((max_y - y_geo) / resolution).astype(np.int64), 0, height - 1)
            flat_idx = row * width + col

            idx_t = torch.from_numpy(flat_idx).to(device)
            z_t = torch.from_numpy(z_geo.astype(np.float32, copy=False)).to(device)
            dsm_flat.scatter_reduce_(0, idx_t, z_t, reduce="amax", include_self=False)
            dsm_min_flat.scatter_reduce_(0, idx_t, z_t, reduce="amin", include_self=False)
            sample_count_flat.scatter_add_(0, idx_t, torch.ones_like(z_t))

            accepted_count = int(flat_idx.size)
            depth_samples_accepted += accepted_count
            image_used = True

        if image_used:
            depth_maps_used += 1

    dsm = dsm_flat.view(height, width)
    raw_dsm = dsm.clone()
    raw_dsm_min = dsm_min_flat.view(height, width)
    sample_count = sample_count_flat.view(height, width)

    # Track which pixels had ACTUAL depth-map support BEFORE gap filling.
    raw_valid = sample_count > 0
    if not raw_valid.any():
        raise RuntimeError(
            f"No valid DSM cells could be accumulated from COLMAP depth maps in {os.path.join(dense_path, 'stereo', 'depth_maps')}"
        )

    # Fix building-edge height spillover: where depth samples span a large
    # range (mixed roof + ground), prefer the LOWER height (= ground).  This
    # prevents oblique-camera roof samples from inflating ground-cell heights
    # and causing tile bleed in the orthophoto.
    dsm_ambiguous_height_m = float(os.getenv("ORTHO_DSM_AMBIGUOUS_HEIGHT_M", "0.5"))
    dsm_ambiguous_fixed_count = 0
    if dsm_ambiguous_height_m > 0:
        ambiguous = raw_valid & ((raw_dsm - raw_dsm_min) > dsm_ambiguous_height_m)
        dsm_ambiguous_fixed_count = int(ambiguous.sum().item())
        dsm[ambiguous] = raw_dsm_min[ambiguous]

    # ── Dual-layer DSM for depth-discontinuity cells ──
    # At cells where the raw depth range spans more than a threshold (e.g. a
    # car roof vs. the ground), keep *both* surfaces so that each camera can
    # texture the layer it actually sees.  The primary (single-surface) DSM
    # is set to the LOWER height (ground) for ortho-grid projection, while
    # dsm_high stores the upper surface (object top).
    layered_dsm_threshold_m = float(os.getenv("ORTHO_DSM_LAYERED_THRESHOLD_M",
                                               str(dsm_ambiguous_height_m)))
    layered_dsm_enabled = str(os.getenv("ORTHO_DSM_LAYERED_ENABLED", "1")).strip().lower() in ("1", "true", "yes", "on")
    # Method 1: per-cell multi-sample spread (works for buildings where oblique
    # cameras deposit both roof + ground samples in the same cell).
    spread_dual = raw_valid & ((raw_dsm - raw_dsm_min) > layered_dsm_threshold_m)
    # Method 2: spatial-gradient detection — catches well-reconstructed elevated
    # objects (cars, walls) where each cell is consistent but neighbouring
    # cells jump sharply.  A 3×3 local range of the resolved DSM reveals
    # depth discontinuities missed by per-cell spread.
    dsm_4d_tmp = dsm.unsqueeze(0).unsqueeze(0)
    valid_4d_tmp = raw_valid.float().unsqueeze(0).unsqueeze(0)
    _neg_large = torch.full_like(dsm_4d_tmp, -1e9)
    _pos_large = torch.full_like(dsm_4d_tmp, 1e9)
    nbr_max_3 = F.max_pool2d(
        torch.where(valid_4d_tmp > 0.5, dsm_4d_tmp, _neg_large),
        kernel_size=3, stride=1, padding=1,
    ).squeeze(0).squeeze(0)
    nbr_min_3 = -F.max_pool2d(
        torch.where(valid_4d_tmp > 0.5, -dsm_4d_tmp, _pos_large),
        kernel_size=3, stride=1, padding=1,
    ).squeeze(0).squeeze(0)
    gradient_dual = raw_valid & ((nbr_max_3 - nbr_min_3) > layered_dsm_threshold_m)
    gradient_added_count = int((gradient_dual & ~spread_dual).sum().item())

    has_dual_layer = spread_dual | gradient_dual
    # Dilate the dual-layer zone so the full transition band (= halo width)
    # is covered.  The halo of an object of height h at view angle θ extends
    # ≈ h·tan(θ)/gsd pixels; for a 1.5 m car at 15° that is ~20 px at
    # 0.02 m GSD.  Default is 15 px.
    layered_dilation_px = max(0, int(os.getenv("ORTHO_DSM_LAYERED_DILATION_PX", "15")))
    if layered_dsm_enabled and layered_dilation_px > 0 and has_dual_layer.any():
        kernel = layered_dilation_px * 2 + 1
        has_dual_layer = F.max_pool2d(
            has_dual_layer.float().unsqueeze(0).unsqueeze(0),
            kernel_size=kernel, stride=1, padding=layered_dilation_px,
        ).squeeze(0).squeeze(0) > 0
        # Only apply where we actually have DSM data
        has_dual_layer = has_dual_layer & raw_valid
    if not layered_dsm_enabled:
        has_dual_layer = torch.zeros_like(raw_valid)
    # dsm_high (object/roof) and dsm_low (ground) for each dual-layer cell.
    # Use a neighbourhood max/min over a window matching the dilation so that
    # dilated cells far from the original edge still get correct surface
    # heights.  raw_dsm gives per-cell max height; raw_dsm_min gives per-cell
    # min height.  Pooling propagates the extreme values into the halo band.
    _pool_r = max(layered_dilation_px, 1)
    _pool_k = _pool_r * 2 + 1
    raw_dsm_4d = raw_dsm.unsqueeze(0).unsqueeze(0)
    raw_min_4d = raw_dsm_min.unsqueeze(0).unsqueeze(0)
    dsm_high = F.max_pool2d(
        torch.where(valid_4d_tmp > 0.5, raw_dsm_4d, _neg_large),
        kernel_size=_pool_k, stride=1, padding=_pool_r,
    ).squeeze(0).squeeze(0)
    dsm_low = -F.max_pool2d(
        torch.where(valid_4d_tmp > 0.5, -raw_min_4d, _pos_large),
        kernel_size=_pool_k, stride=1, padding=_pool_r,
    ).squeeze(0).squeeze(0)
    del dsm_4d_tmp, valid_4d_tmp, _neg_large, _pos_large, raw_dsm_4d, raw_min_4d
    # Outside the dual-layer zone, both layers equal the primary DSM
    dsm_high[~has_dual_layer] = dsm[~has_dual_layer]
    dsm_low[~has_dual_layer] = dsm[~has_dual_layer]
    dual_layer_count = int(has_dual_layer.sum().item())

    # ── DSM morphological opening for thin halo removal ──
    # Elevated objects (cars, pillars) spill their height into neighbouring
    # ground cells because depth samples from oblique views land 1-2 px wide.
    # A morphological opening (erosion then dilation) on the height contrast
    # removes these thin halos while preserving larger structures (buildings).
    dsm_morph_radius = max(0, int(os.getenv("ORTHO_DSM_MORPH_OPENING_PX", "0")))
    dsm_morph_fixed_count = 0
    if dsm_morph_radius > 0 and raw_valid.any():
        # Compute local min-max range in a 3x3 neighbourhood
        kernel = dsm_morph_radius * 2 + 1
        dsm_4d = dsm.unsqueeze(0).unsqueeze(0)
        valid_4d = raw_valid.float().unsqueeze(0).unsqueeze(0)
        neg_large = torch.full_like(dsm_4d, -1.0e9)
        pos_large = torch.full_like(dsm_4d, 1.0e9)
        local_max = F.max_pool2d(torch.where(valid_4d > 0.5, dsm_4d, neg_large), kernel_size=kernel, stride=1, padding=dsm_morph_radius)
        local_min = -F.max_pool2d(torch.where(valid_4d > 0.5, -dsm_4d, pos_large), kernel_size=kernel, stride=1, padding=dsm_morph_radius)
        local_range = (local_max - local_min).squeeze(0).squeeze(0)
        # Erode: cells isolated at an elevated height get replaced by local min
        # Only apply where the cell protrudes above all neighbours by >0.3m
        # and the cell has limited sample support (thin features)
        thin_elevated = raw_valid & (local_range > 0.3) & (sample_count <= 8)
        if thin_elevated.any():
            dsm_morph_fixed_count = int(thin_elevated.sum().item())
            dsm[thin_elevated] = local_min.squeeze(0).squeeze(0)[thin_elevated]
        del dsm_4d, valid_4d, neg_large, pos_large, local_max, local_min, local_range

    # ── Sliding-window DSM anomaly detection (inspired by Ge et al. 2026) ──
    # For each cell, check a local window. If >30% of valid neighbours differ
    # from this cell by more than the threshold, the cell is likely an
    # elevation outlier (e.g. roof height spilling onto ground cells at
    # building edges). Replace with the local median of valid neighbours.
    dsm_anomaly_window = max(3, int(os.getenv("ORTHO_DSM_ANOMALY_WINDOW", "5")))
    dsm_anomaly_threshold_m = float(os.getenv("ORTHO_DSM_ANOMALY_THRESHOLD_M", "0.5"))
    dsm_anomaly_ratio = float(os.getenv("ORTHO_DSM_ANOMALY_RATIO", "0.3"))
    dsm_anomaly_fixed_count = 0
    if dsm_anomaly_threshold_m > 0 and dsm_anomaly_window >= 3:
        pad = dsm_anomaly_window // 2
        dsm_4d = dsm.unsqueeze(0).unsqueeze(0)  # (1, 1, H, W)
        valid_4d = raw_valid.float().unsqueeze(0).unsqueeze(0)

        # Unfold: extract all (window x window) patches for every pixel
        # Shape: (1, 1, H, W, win, win)
        dsm_patches = F.unfold(dsm_4d, kernel_size=dsm_anomaly_window, padding=pad)  # (1, win*win, H*W)
        valid_patches = F.unfold(valid_4d, kernel_size=dsm_anomaly_window, padding=pad)
        n_elements = dsm_anomaly_window * dsm_anomaly_window

        # Centre pixel values (broadcast-ready)
        centre_vals = dsm.view(1, 1, -1)  # (1, 1, H*W)

        # Count valid neighbours that differ by > threshold from centre
        diff = torch.abs(dsm_patches - centre_vals)
        differs = (diff > dsm_anomaly_threshold_m) & (valid_patches > 0.5)
        n_valid_neighbours = (valid_patches > 0.5).sum(dim=1, keepdim=True).float()
        n_differs = differs.sum(dim=1, keepdim=True).float()

        # Ratio of disagreeing neighbours
        ratio = torch.where(
            n_valid_neighbours > 0,
            n_differs / n_valid_neighbours,
            torch.zeros_like(n_differs),
        ).view(height, width)

        anomaly_mask = raw_valid & (ratio > dsm_anomaly_ratio)

        if anomaly_mask.any():
            # Replace anomalous cells with local median of valid neighbours.
            # Compute approximate median: sort the patch values per pixel and
            # take the middle valid element.  For efficiency, use the local
            # minimum as a conservative substitute (avoids heavy sorting).
            # The min-of-valid-neighbours is the same strategy as the 8-dir
            # fill in Ge et al. (prefer low ground height over roof spill).
            valid_dsm_only = torch.where(valid_patches > 0.5, dsm_patches, torch.full_like(dsm_patches, float("inf")))
            local_min = valid_dsm_only.min(dim=1).values.view(height, width)  # (H, W)
            dsm_anomaly_fixed_count = int(anomaly_mask.sum().item())
            dsm[anomaly_mask] = local_min[anomaly_mask]

        del dsm_patches, valid_patches, dsm_4d, valid_4d, diff, differs

    fill_iterations = int(os.getenv("ORTHO_DSM_FILL_ITERATIONS", "5"))

    # Gap-fill for small holes only and keep track of how far each filled
    # pixel had to expand away from actual point support.
    dsm, support_distance_px = fill_dsm_gaps_gpu(dsm, NODATA, iterations=fill_iterations)

    surface_normals = estimate_surface_normals_gpu(dsm, resolution)

    # ---- Angle-aware camera assignment ----
    primary_best_score = torch.full((height, width), -1.0, dtype=torch.float64, device=device)
    primary_voronoi_map = torch.full((height, width), -1, dtype=torch.long, device=device)
    fallback_best_score = torch.full((height, width), -1.0, dtype=torch.float64, device=device)
    fallback_voronoi_map = torch.full((height, width), -1, dtype=torch.long, device=device)

    grid_row, grid_col = torch.meshgrid(
        torch.arange(height, device=device, dtype=torch.float64),
        torch.arange(width, device=device, dtype=torch.float64),
        indexing='ij',
    )
    world_gx = grid_col * resolution + min_x
    world_gy = max_y - grid_row * resolution

    filled_valid = dsm > NODATA
    max_support_distance_px = float(os.getenv("ORTHO_DSM_MAX_SUPPORT_DISTANCE_PX", "-1"))
    if max_support_distance_px < 0:
        valid_mask = filled_valid
    else:
        valid_mask = filled_valid & (support_distance_px <= max_support_distance_px)

    # Edge detection — still computed for diagnostics, but no longer removes
    # pixels from valid_mask.  The per-pixel Z-buffer depth check during
    # warping (see _warp_image) handles occlusion precisely.
    edge_depth_range_m = float(os.getenv("ORTHO_DSM_EDGE_DEPTH_RANGE_M", "0.20"))
    edge_min_raw_support = float(os.getenv("ORTHO_DSM_EDGE_MIN_RAW_SUPPORT", "4"))
    edge_dilation_px = max(0, int(os.getenv("ORTHO_DSM_EDGE_DILATION_PX", "1")))
    edge_assignment_dilation_px = max(edge_dilation_px, int(os.getenv("ORTHO_DSM_EDGE_ASSIGNMENT_DILATION_PX", "3")))
    edge_source_depth_tolerance_m = float(os.getenv("ORTHO_DSM_EDGE_SOURCE_DEPTH_TOLERANCE_M", "0.08"))
    edge_source_depth_neighborhood_px = max(0, int(os.getenv("ORTHO_DSM_EDGE_SOURCE_DEPTH_NEIGHBORHOOD_PX", "1")))
    edge_primary_max_view_angle_deg = float(os.getenv("ORTHO_DSM_EDGE_PRIMARY_MAX_VIEW_ANGLE_DEG", str(NADIR_THRESHOLD_DEG)))
    edge_fallback_max_view_angle_deg = float(os.getenv("ORTHO_DSM_EDGE_FALLBACK_MAX_VIEW_ANGLE_DEG", str(FALLBACK_NADIR_THRESHOLD_DEG)))
    edge_assignment_max_support_distance_px = float(os.getenv("ORTHO_DSM_EDGE_ASSIGNMENT_MAX_SUPPORT_DISTANCE_PX", "-1"))
    edge_raw_depth_spread_m = float(os.getenv("ORTHO_DSM_EDGE_RAW_DEPTH_SPREAD_M", str(edge_depth_range_m)))
    edge_raw_depth_spread_min_samples = float(os.getenv("ORTHO_DSM_EDGE_RAW_DEPTH_SPREAD_MIN_SAMPLES", "2"))
    edge_occlusion_buffer_px = max(0, int(os.getenv("ORTHO_DSM_EDGE_OCCLUSION_BUFFER_PX", "0")))
    raw_edge_mask = torch.zeros_like(raw_valid)
    edge_sensitive_mask = torch.zeros_like(raw_valid)
    edge_assignment_mask = torch.zeros_like(raw_valid)
    edge_occlusion_mask = torch.zeros_like(raw_valid)
    edge_rejected_mask = torch.zeros_like(raw_valid)
    edge_assignment_paint_rejected_mask = torch.zeros_like(raw_valid)
    edge_raw_mixed_depth_rejected_mask = torch.zeros_like(raw_valid)
    edge_source_visibility_checked_count = 0
    edge_source_visibility_rejected_count = 0
    if edge_depth_range_m > 0.0 and raw_valid.any():
        raw_height = raw_dsm.unsqueeze(0).unsqueeze(0)
        raw_valid_4d = raw_valid.unsqueeze(0).unsqueeze(0)
        neg_large = torch.full_like(raw_height, -1.0e9)

        local_max = F.max_pool2d(torch.where(raw_valid_4d, raw_height, neg_large), kernel_size=3, stride=1, padding=1)
        local_min = -F.max_pool2d(torch.where(raw_valid_4d, -raw_height, neg_large), kernel_size=3, stride=1, padding=1)
        local_depth_range = (local_max - local_min).squeeze(0).squeeze(0)
        raw_edge_mask = raw_valid & (local_depth_range >= edge_depth_range_m)

        boundary_neighborhood = raw_edge_mask
        if edge_dilation_px > 0 and raw_edge_mask.any():
            kernel_size = edge_dilation_px * 2 + 1
            boundary_neighborhood = F.max_pool2d(
                raw_edge_mask.float().unsqueeze(0).unsqueeze(0),
                kernel_size=kernel_size,
                stride=1,
                padding=edge_dilation_px,
            ).squeeze(0).squeeze(0) > 0

        edge_sensitive_mask = boundary_neighborhood
        edge_assignment_mask = boundary_neighborhood
        if edge_assignment_dilation_px > edge_dilation_px and raw_edge_mask.any():
            assignment_kernel_size = edge_assignment_dilation_px * 2 + 1
            edge_assignment_mask = F.max_pool2d(
                raw_edge_mask.float().unsqueeze(0).unsqueeze(0),
                kernel_size=assignment_kernel_size,
                stride=1,
                padding=edge_assignment_dilation_px,
            ).squeeze(0).squeeze(0) > 0
        if edge_occlusion_buffer_px > 0 and raw_edge_mask.any():
            occlusion_kernel_size = edge_occlusion_buffer_px * 2 + 1
            edge_occlusion_mask = F.max_pool2d(
                raw_edge_mask.float().unsqueeze(0).unsqueeze(0),
                kernel_size=occlusion_kernel_size,
                stride=1,
                padding=edge_occlusion_buffer_px,
            ).squeeze(0).squeeze(0) > 0
        edge_rejected_mask = boundary_neighborhood & (
            (~raw_valid)
            | ((sample_count < edge_min_raw_support) & raw_valid)
        )
        # NOTE: edge_rejected_mask, edge_occlusion_mask, mixed_depth_rejected_mask
        # are computed for diagnostics only — they no longer remove pixels from
        # valid_mask.  The per-pixel Z-buffer warp check handles occlusion.

    edge_raw_mixed_depth_rejected_mask = build_mixed_depth_rejection_mask(
        raw_valid,
        edge_assignment_mask,
        sample_count,
        raw_dsm_min,
        raw_dsm,
        edge_raw_depth_spread_m,
        edge_raw_depth_spread_min_samples,
    )

    # paintable_mask = valid_mask (no edge-paint restriction with Z-buffer)
    paintable_mask = valid_mask
    edge_assignment_paint_rejected_mask = torch.zeros_like(raw_valid)

    if transform_data:
        R_inv = np.linalg.inv(R_t)
        R_inv_t = torch.tensor(R_inv, device=device, dtype=torch.float64)
        t_tensor = torch.tensor(t_vec, device=device, dtype=torch.float64)
        pts_geo = torch.stack([world_gx, world_gy, dsm.double()], dim=-1)
        pts_local_full = ((pts_geo - t_tensor) / float(scale)) @ R_inv_t.T
    else:
        pts_local_full = torch.stack([world_gx, world_gy, dsm.double()], dim=-1)

    primary_incidence_min = float(os.getenv("ORTHO_DSM_PRIMARY_INCIDENCE_MIN", "0.25"))
    primary_axis_alignment_min = float(os.getenv("ORTHO_DSM_PRIMARY_AXIS_ALIGNMENT_MIN", "0.35"))
    primary_nadirness_min = float(os.getenv("ORTHO_DSM_PRIMARY_NADIRNESS_MIN", "0.6"))
    fallback_incidence_min = float(os.getenv("ORTHO_DSM_FALLBACK_INCIDENCE_MIN", "0.12"))
    fallback_axis_alignment_min = float(os.getenv("ORTHO_DSM_FALLBACK_AXIS_ALIGNMENT_MIN", "0.12"))
    fallback_nadirness_min = float(os.getenv("ORTHO_DSM_FALLBACK_NADIRNESS_MIN", "0.05"))

    for img_id, image in reconstruction.images.items():
        if img_id not in valid_images and img_id not in fallback_images:
            continue
        cam_center_local = np.asarray(image.projection_center(), dtype=np.float64)
        R_cw, t_cw = _get_rotation_and_translation(image)
        optical_axis_local = np.asarray(R_cw[2, :], dtype=np.float64)

        if transform_data:
            c_geo = scale * (R_t @ cam_center_local) + t_vec
            optical_axis_geo = R_t @ optical_axis_local
        else:
            c_geo = cam_center_local
            optical_axis_geo = optical_axis_local

        optical_axis_geo = optical_axis_geo / max(np.linalg.norm(optical_axis_geo), 1e-12)

        cx, cy, cz = float(c_geo[0]), float(c_geo[1]), float(c_geo[2])

        ray_to_cam_x = cx - world_gx
        ray_to_cam_y = cy - world_gy
        ray_to_cam_z = cz - dsm
        ray_norm = torch.sqrt(ray_to_cam_x ** 2 + ray_to_cam_y ** 2 + ray_to_cam_z ** 2).clamp(min=1e-6)

        ray_to_cam = torch.stack([
            ray_to_cam_x / ray_norm,
            ray_to_cam_y / ray_norm,
            ray_to_cam_z / ray_norm,
        ], dim=-1)

        nadirness = ray_to_cam[..., 2].clamp(min=0.0)

        incidence = (surface_normals * ray_to_cam).sum(dim=-1).clamp(min=0.0)
        axis_alignment = (
            (-ray_to_cam[..., 0] * float(optical_axis_geo[0]))
            + (-ray_to_cam[..., 1] * float(optical_axis_geo[1]))
            + (-ray_to_cam[..., 2] * float(optical_axis_geo[2]))
        ).clamp(min=0.0)

        score = incidence * axis_alignment * nadirness

        # Only allow cameras to win pixels that actually project inside the
        # image footprint. The previous scorer ignored this and assigned many
        # fallback pixels to images where they later projected out of bounds.
        camera = reconstruction.cameras[image.camera_id]
        model = camera.model_name if hasattr(camera, 'model_name') else str(camera.model)
        projection_in_bounds = torch.zeros_like(valid_mask, dtype=torch.bool)
        if model in ("PINHOLE", "SIMPLE_PINHOLE", "0", "1"):
            local_x = pts_local_full[..., 0]
            local_y = pts_local_full[..., 1]
            local_z = pts_local_full[..., 2]

            cam_x = (
                float(R_cw[0, 0]) * local_x
                + float(R_cw[0, 1]) * local_y
                + float(R_cw[0, 2]) * local_z
                + float(t_cw[0])
            )
            cam_y = (
                float(R_cw[1, 0]) * local_x
                + float(R_cw[1, 1]) * local_y
                + float(R_cw[1, 2]) * local_z
                + float(t_cw[1])
            )
            cam_z = (
                float(R_cw[2, 0]) * local_x
                + float(R_cw[2, 1]) * local_y
                + float(R_cw[2, 2]) * local_z
                + float(t_cw[2])
            )
            front = cam_z > 1e-6

            fx = float(camera.params[0])
            if model in ("PINHOLE", "1"):
                fy = float(camera.params[1])
                px = float(camera.params[2])
                py = float(camera.params[3])
            else:
                fy = fx
                px = float(camera.params[1])
                py = float(camera.params[2])

            u = torch.zeros_like(cam_x)
            v = torch.zeros_like(cam_y)
            u[front] = (cam_x[front] / cam_z[front]) * fx + px
            v[front] = (cam_y[front] / cam_z[front]) * fy + py
            projection_in_bounds = (
                front
                & (u >= 0.0)
                & (u < float(camera.width))
                & (v >= 0.0)
                & (v < float(camera.height))
            )
        else:
            projection_in_bounds = valid_mask

        # With Z-buffer warp check, allow all in-bounds projections for scoring.
        # The warp-time depth check will reject occluded pixels precisely.
        projection_allowed = projection_in_bounds

        if img_id in valid_images:
            primary_score = torch.where(
                (incidence > primary_incidence_min)
                & (axis_alignment > primary_axis_alignment_min)
                & (nadirness > primary_nadirness_min)
                & projection_allowed
                & paintable_mask,
                score,
                torch.full_like(score, -1.0),
            )
            better = primary_score > primary_best_score
            primary_best_score[better] = primary_score[better]
            primary_voronoi_map[better] = img_id

        if img_id in fallback_images:
            fallback_score = torch.where(
                (incidence > fallback_incidence_min)
                & (axis_alignment > fallback_axis_alignment_min)
                & (nadirness > fallback_nadirness_min)
                & projection_allowed
                & paintable_mask,
                score,
                torch.full_like(score, -1.0),
            )
            better = fallback_score > fallback_best_score
            fallback_best_score[better] = fallback_score[better]
            fallback_voronoi_map[better] = img_id

    raw_valid_count = int(raw_valid.sum().item())
    filled_valid_count = int(filled_valid.sum().item())
    valid_count = int(valid_mask.sum().item())
    finite_support_distances = support_distance_px[filled_valid].to(torch.int64)
    support_distance_histogram = {
        str(distance): int((finite_support_distances == distance).sum().item())
        for distance in range(int(finite_support_distances.max().item()) + 1)
    } if finite_support_distances.numel() > 0 else {}

    diagnostics = {
        "raw_valid_count": raw_valid_count,
        "gap_filled_valid_count": filled_valid_count,
        "gap_filled_added_count": filled_valid_count - raw_valid_count,
        "usable_valid_count": valid_count,
        "support_limited_rejected_count": filled_valid_count - valid_count,
        "edge_depth_range_m": edge_depth_range_m,
        "edge_min_raw_support": edge_min_raw_support,
        "edge_dilation_px": edge_dilation_px,
        "edge_assignment_dilation_px": edge_assignment_dilation_px,
        "edge_occlusion_buffer_px": edge_occlusion_buffer_px,
        "raw_edge_pixel_count": int(raw_edge_mask.sum().item()),
        "edge_sensitive_pixel_count": int(edge_sensitive_mask.sum().item()),
        "edge_assignment_pixel_count": int(edge_assignment_mask.sum().item()),
        "edge_occlusion_pixel_count": int(edge_occlusion_mask.sum().item()),
        "edge_rejected_count": int(edge_rejected_mask.sum().item()),
        "edge_source_depth_tolerance_m": edge_source_depth_tolerance_m,
        "edge_source_depth_neighborhood_px": edge_source_depth_neighborhood_px,
        "edge_assignment_max_support_distance_px": edge_assignment_max_support_distance_px,
        "edge_raw_depth_spread_m": edge_raw_depth_spread_m,
        "edge_raw_depth_spread_min_samples": edge_raw_depth_spread_min_samples,
        "edge_source_visibility_checked_count": edge_source_visibility_checked_count,
        "edge_source_visibility_rejected_count": edge_source_visibility_rejected_count,
        "edge_assignment_paint_rejected_count": int(edge_assignment_paint_rejected_mask.sum().item()),
        "edge_raw_mixed_depth_rejected_count": int(edge_raw_mixed_depth_rejected_mask.sum().item()),
        "edge_primary_max_view_angle_deg": edge_primary_max_view_angle_deg,
        "edge_fallback_max_view_angle_deg": edge_fallback_max_view_angle_deg,
        "dsm_ambiguous_height_m": dsm_ambiguous_height_m,
        "dsm_ambiguous_fixed_count": dsm_ambiguous_fixed_count,
        "layered_dsm_enabled": layered_dsm_enabled,
        "layered_dsm_threshold_m": layered_dsm_threshold_m,
        "layered_dilation_px": layered_dilation_px,
        "dual_layer_pixel_count": dual_layer_count,
        "dual_layer_spread_count": int(spread_dual.sum().item()),
        "dual_layer_gradient_added_count": gradient_added_count,
        "dsm_anomaly_window": dsm_anomaly_window,
        "dsm_anomaly_threshold_m": dsm_anomaly_threshold_m,
        "dsm_anomaly_ratio": dsm_anomaly_ratio,
        "dsm_anomaly_fixed_count": dsm_anomaly_fixed_count,
        "dsm_morph_opening_px": dsm_morph_radius,
        "dsm_morph_fixed_count": dsm_morph_fixed_count,
        "max_support_distance_px": max_support_distance_px,
        "fill_iterations": fill_iterations,
        "depth_map_type": depth_map_type,
        "depth_stride": depth_stride,
        "depth_maps_found": depth_maps_found,
        "depth_maps_used": depth_maps_used,
        "normal_maps_used": normal_maps_used,
        "depth_samples_considered": depth_samples_considered,
        "depth_samples_accepted": depth_samples_accepted,
        "dsm_build_max_view_angle_deg": dsm_build_max_view_angle_deg,
        "dsm_source_image_count": len(dsm_source_image_ids),
        "dsm_source_images_skipped_by_angle_count": len(reconstruction.images) - len(dsm_source_image_ids),
        "support_distance_histogram": support_distance_histogram,
        "image_angles_deg": image_angles_deg,
        "primary_thresholds": {
            "nadir_deg": NADIR_THRESHOLD_DEG,
            "incidence_min": primary_incidence_min,
            "axis_alignment_min": primary_axis_alignment_min,
            "nadirness_min": primary_nadirness_min,
        },
        "fallback_thresholds": {
            "nadir_deg": FALLBACK_NADIR_THRESHOLD_DEG,
            "incidence_min": fallback_incidence_min,
            "axis_alignment_min": fallback_axis_alignment_min,
            "nadirness_min": fallback_nadirness_min,
        },
    }

    return (
        dsm,
        raw_valid,
        support_distance_px,
        primary_voronoi_map,
        fallback_voronoi_map,
        valid_mask,
        edge_sensitive_mask,
        edge_assignment_mask,
        min_x,
        max_y,
        width,
        height,
        set(valid_images),
        set(fallback_images),
        diagnostics,
        primary_best_score,
        has_dual_layer,
        dsm_high,
        dsm_low,
    )


# ---------------------------------------------------------------------------
#  Main entry point
# ---------------------------------------------------------------------------

def generate_true_orthophoto_pytorch(dense_path, ortho_file, utm_crs, vol_id,
                                     transform_file=None, report_fn=None, resolution=0.05):
    """
    Generate a True Orthophoto from COLMAP dense stereo depth maps.

    Depth maps are reprojected directly into an ortho-grid DSM, avoiding the
    lossy fused.ply -> raster round-trip. Each source image is then warped
    through that DSM one at a time (O(1) VRAM), and each pixel is assigned to
    the camera with the best view score. The final mosaic is written as a
    compressed GeoTIFF.
    """
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    report(vol_id, "ORTHO", 95, f"Starting depth-map True Orthophoto on {device}…", report_fn)

    depth_maps_dir = os.path.join(dense_path, "stereo", "depth_maps")
    if not os.path.isdir(depth_maps_dir):
        raise FileNotFoundError(f"Missing COLMAP depth map directory: {depth_maps_dir}")

    transform_data = None
    if transform_file and os.path.exists(transform_file):
        with open(transform_file, 'r') as tf:
            transform_data = json.load(tf)


    report(vol_id, "ORTHO", 96, "Building 2.5D DSM from COLMAP depth maps and angle-aware camera map…", report_fn)
    reconstruction = pycolmap.Reconstruction(os.path.join(dense_path, "sparse"))

    dsm, raw_valid_mask, support_distance_px, voronoi_map, fallback_voronoi_map, valid_dsm_mask, edge_sensitive_mask, edge_assignment_mask, min_x, max_y, width, height, valid_images, fallback_images, build_diagnostics, primary_best_score, has_dual_layer, dsm_high, dsm_low = build_dsm_and_voronoi(
        dense_path, reconstruction, transform_data, resolution, device,
    )

    total_pixels = max(1, width * height)
    raw_valid_count = int(raw_valid_mask.sum().item())
    filled_dsm_count = int((dsm > NODATA).sum().item())
    valid_dsm_count = int(valid_dsm_mask.sum().item())
    report(
        vol_id,
        "ORTHO",
        96,
        (
            f"DSM support: raw point-backed pixels={raw_valid_count}/{total_pixels} ({100.0 * raw_valid_count / total_pixels:.1f}%), "
            f"gap-filled reachable pixels={filled_dsm_count}/{total_pixels} ({100.0 * filled_dsm_count / total_pixels:.1f}%), "
            f"usable after support cap={valid_dsm_count}/{total_pixels} ({100.0 * valid_dsm_count / total_pixels:.1f}%), "
            f"rejected far-fill={filled_dsm_count - valid_dsm_count}"
        ),
        report_fn,
    )

    diagnostics_path = f"{ortho_file}.diagnostics.json"
    primary_assigned_counts = {}
    fallback_assigned_counts = {}
    image_stats = {}
    for img_id, image in reconstruction.images.items():
        primary_assigned = int(((voronoi_map == img_id) & valid_dsm_mask).sum().item())
        fallback_assigned = int(((fallback_voronoi_map == img_id) & valid_dsm_mask).sum().item())
        primary_assigned_counts[img_id] = primary_assigned
        fallback_assigned_counts[img_id] = fallback_assigned
        image_stats[img_id] = {
            "image_name": image.name,
            "primary_assigned_pixels": primary_assigned,
            "fallback_assigned_pixels": fallback_assigned,
            "primary_painted_pixels": 0,
            "fallback_painted_pixels": 0,
            "angle_from_down_deg": build_diagnostics["image_angles_deg"].get(img_id),
            "primary_eligible": img_id in valid_images,
            "fallback_eligible": img_id in fallback_images,
        }

    top_primary = sorted(
        (stats for stats in image_stats.values() if stats["primary_assigned_pixels"] > 0),
        key=lambda stats: stats["primary_assigned_pixels"],
        reverse=True,
    )[:10]
    if top_primary:
        report(
            vol_id,
            "ORTHO",
            96,
            "Primary assignment top images: " + "; ".join(
                f"{stats['image_name']}={stats['primary_assigned_pixels']} px"
                for stats in top_primary
            ),
            report_fn,
        )

    top_fallback = sorted(
        (stats for stats in image_stats.values() if stats["fallback_assigned_pixels"] > 0),
        key=lambda stats: stats["fallback_assigned_pixels"],
        reverse=True,
    )[:10]
    if top_fallback:
        report(
            vol_id,
            "ORTHO",
            96,
            "Fallback assignment top images: " + "; ".join(
                f"{stats['image_name']}={stats['fallback_assigned_pixels']} px"
                for stats in top_fallback
            ),
            report_fn,
        )

    # Output buffer (initialized to 0 = black)
    ortho_rgb = torch.zeros((3, height, width), dtype=torch.uint8, device=device)
    # Track which pixels have been successfully painted
    painted = torch.zeros((height, width), dtype=torch.bool, device=device)

    # ── Dual-layer ortho buffers (high = object surface) ──
    dual_layer_count = int(has_dual_layer.sum().item())
    layered_dsm_active = dual_layer_count > 0
    if layered_dsm_active:
        ortho_rgb_high = torch.zeros((3, height, width), dtype=torch.uint8, device=device)
        painted_high = torch.zeros((height, width), dtype=torch.bool, device=device)
        report(vol_id, "ORTHO", 96, f"Dual-layer DSM active: {dual_layer_count} pixels with separate high/low surfaces.", report_fn)
    else:
        ortho_rgb_high = None
        painted_high = None

    # World coordinate grids
    grid_row, grid_col = torch.meshgrid(
        torch.arange(height, device=device, dtype=torch.float64),
        torch.arange(width, device=device, dtype=torch.float64),
        indexing='ij',
    )
    world_x = grid_col * resolution + min_x
    world_y = max_y - grid_row * resolution
    world_z = dsm
    world_z_high = dsm_high
    world_z_low = dsm_low

    # Inverse Sim3 to go from geographic back to COLMAP local coords
    if transform_data:
        R_np = np.array(transform_data["R"], dtype=np.float64)
        scale_val = float(transform_data["scale"])
        t_np = np.array(transform_data["t"], dtype=np.float64)
        R_inv = np.linalg.inv(R_np)

        R_inv_t = torch.tensor(R_inv, device=device, dtype=torch.float64)
        t_t = torch.tensor(t_np, device=device, dtype=torch.float64)

        def geo_to_local(x, y, z):
            """(N,) tensors → (N, 3) float64 local coords."""
            pts = torch.stack([x.double(), y.double(), z.double()], dim=-1)
            pts = (pts - t_t) / scale_val
            return (R_inv_t @ pts.T).T
    else:
        def geo_to_local(x, y, z):
            return torch.stack([x.double(), y.double(), z.double()], dim=-1)

    images_dir = os.path.join(dense_path, "images")
    total_images = len(reconstruction.images)

    primary_margin_px = int(os.getenv("ORTHO_DSM_IMAGE_MARGIN_PX", "0"))
    fallback_margin_px = int(os.getenv("ORTHO_DSM_FALLBACK_MARGIN_PX", "0"))

    def _project_pixels_to_image(image, pixel_mask, margin_px=None, z_tensor=None):
        """Project DSM pixels into an image and return projection diagnostics.
        z_tensor overrides world_z for the projection (used for dual-layer)."""
        n_pixels = int(pixel_mask.sum())
        if n_pixels == 0:
            empty_indices = torch.empty((0, 2), dtype=torch.long, device=device)
            return {
                "count": 0,
                "mask_indices": empty_indices,
                "valid_indices": empty_indices,
                "front_mask": torch.empty((0,), dtype=torch.bool, device=device),
                "in_bounds_mask": torch.empty((0,), dtype=torch.bool, device=device),
                "u_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "v_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "cam_z_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "w_img": 0,
                "h_img": 0,
                "img_path": None,
                "image_missing": False,
            }

        z_src = z_tensor if z_tensor is not None else world_z
        pts_local = geo_to_local(world_x[pixel_mask], world_y[pixel_mask], z_src[pixel_mask])
        mask_indices = pixel_mask.nonzero(as_tuple=False)

        camera = reconstruction.cameras[image.camera_id]
        R_cw_np, t_cw_np = _get_rotation_and_translation(image)
        R_cw = torch.tensor(R_cw_np, dtype=torch.float64, device=device)
        t_cw = torch.tensor(t_cw_np, dtype=torch.float64, device=device)

        pts_cam = (R_cw @ pts_local.T).T + t_cw

        front = pts_cam[:, 2] > 0
        if not front.any():
            return {
                "count": n_pixels,
                "mask_indices": mask_indices,
                "valid_indices": torch.empty((0, 2), dtype=torch.long, device=device),
                "front_mask": front,
                "in_bounds_mask": torch.empty((0,), dtype=torch.bool, device=device),
                "u_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "v_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "cam_z_valid": torch.empty((0,), dtype=torch.float64, device=device),
                "w_img": int(camera.width),
                "h_img": int(camera.height),
                "img_path": os.path.join(images_dir, image.name),
                "image_missing": not os.path.exists(os.path.join(images_dir, image.name)),
            }

        pts_cam_f = pts_cam[front]
        cam_z_front = pts_cam_f[:, 2]

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

            u = (pts_cam_f[:, 0] / cam_z_front) * fx + cx
            v = (pts_cam_f[:, 1] / cam_z_front) * fy + cy
        else:
            pts_cam_cpu = pts_cam_f.cpu().numpy()
            uv_cpu = np.array([camera.img_from_cam(p) for p in pts_cam_cpu])
            u = torch.tensor(uv_cpu[:, 0], device=device, dtype=torch.float64)
            v = torch.tensor(uv_cpu[:, 1], device=device, dtype=torch.float64)

        w_img = int(camera.width)
        h_img = int(camera.height)

        # Stay away from image borders to reduce edge bleed. The fallback pass
        # can use a smaller margin to recover small gaps.
        margin = primary_margin_px if margin_px is None else int(margin_px)
        in_bounds = (u >= margin) & (u < w_img - margin) & (v >= margin) & (v < h_img - margin)
        img_path = os.path.join(images_dir, image.name)
        valid_indices = mask_indices[front][in_bounds] if in_bounds.any() else torch.empty((0, 2), dtype=torch.long, device=device)

        return {
            "count": n_pixels,
            "mask_indices": mask_indices,
            "valid_indices": valid_indices,
            "front_mask": front,
            "in_bounds_mask": in_bounds,
            "u_valid": u[in_bounds] if in_bounds.any() else torch.empty((0,), dtype=torch.float64, device=device),
            "v_valid": v[in_bounds] if in_bounds.any() else torch.empty((0,), dtype=torch.float64, device=device),
            "cam_z_valid": cam_z_front[in_bounds] if in_bounds.any() else torch.empty((0,), dtype=torch.float64, device=device),
            "w_img": w_img,
            "h_img": h_img,
            "img_path": img_path,
            "image_missing": not os.path.exists(img_path),
        }

    warp_depth_check = str(os.getenv("ORTHO_DSM_WARP_DEPTH_CHECK", "1")).strip().lower() in ("1", "true", "yes", "on")
    warp_depth_tolerance_m = float(os.getenv("ORTHO_DSM_WARP_DEPTH_TOLERANCE_M", "0.3"))
    warp_depth_neighborhood_px = max(0, int(os.getenv("ORTHO_DSM_WARP_DEPTH_NEIGHBORHOOD_PX", "0")))
    warp_depth_check_fallback = str(os.getenv("ORTHO_DSM_WARP_DEPTH_CHECK_FALLBACK", "1")).strip().lower() in ("1", "true", "yes", "on")
    warp_depth_occluded_total = 0
    warp_depth_checked_total = 0
    warp_depth_edge_rejected_total = 0
    depth_map_type_warp = str(os.getenv("ORTHO_DSM_DEPTH_MAP_TYPE", "geometric")).strip().lower() or "geometric"
    # Depth-edge gradient threshold: reject projections landing on depth
    # discontinuities in the source image to prevent bilinear bleed artefacts.
    warp_depth_edge_check = str(os.getenv("ORTHO_DSM_WARP_DEPTH_EDGE_CHECK", "1")).strip().lower() in ("1", "true", "yes", "on")
    warp_depth_edge_gradient = float(os.getenv("ORTHO_DSM_WARP_DEPTH_EDGE_GRADIENT", "0.15"))

    # Multi-camera blending buffers: accumulate weighted colors from multiple
    # cameras to average out per-camera noise and sampling artefacts (Gharibi
    # & Habib 2018 weighted averaging).
    enable_blending = str(os.getenv("ORTHO_DSM_ENABLE_BLENDING", "1")).strip().lower() in ("1", "true", "yes", "on")
    blend_rgb = torch.zeros((3, height, width), dtype=torch.float32, device=device)
    blend_weight = torch.zeros((height, width), dtype=torch.float32, device=device)
    # High-layer blending buffers for dual-layer cells
    if layered_dsm_active and enable_blending:
        blend_rgb_high = torch.zeros((3, height, width), dtype=torch.float32, device=device)
        blend_weight_high = torch.zeros((height, width), dtype=torch.float32, device=device)
    else:
        blend_rgb_high = None
        blend_weight_high = None
    layered_warp_high_painted = 0
    layered_warp_low_painted = 0
    layered_layer_tolerance_m = float(os.getenv("ORTHO_DSM_LAYERED_LAYER_TOLERANCE_M",
                                                 str(warp_depth_tolerance_m)))

    def _warp_image(img_id, image, pixel_mask, margin_px=None, use_depth_check=None, score_tensor=None):
        """Warp one image onto the DSM for pixels in pixel_mask.
        use_depth_check overrides the global warp_depth_check when set.
        score_tensor is an optional (H, W) tensor of per-pixel camera scores
        used as blending weights when enable_blending is on.

        When the dual-layer DSM is active, pixels in the has_dual_layer zone
        are projected through both surface layers.  The source depth map
        determines which layer the camera actually sees, and each layer is
        textured into its own buffer.  This eliminates the halo that results
        from forcing a single surface at depth discontinuities.
        """
        nonlocal warp_depth_occluded_total, warp_depth_checked_total, warp_depth_edge_rejected_total
        nonlocal layered_warp_high_painted, layered_warp_low_painted

        # ---- Split into single-layer and dual-layer subsets ----
        if layered_dsm_active:
            single_mask = pixel_mask & (~has_dual_layer)
            dual_mask = pixel_mask & has_dual_layer
        else:
            single_mask = pixel_mask
            dual_mask = None

        total_painted = 0

        # ---- Helper: depth-check + edge-check + sample + paint ----
        def _depth_filter_and_paint(valid_indices, u_valid, v_valid, cam_z_valid,
                                    image, do_depth_check, img_path, w_img, h_img,
                                    target_rgb, target_painted, target_blend_rgb,
                                    target_blend_weight, score_tensor):
            """Run Z-buffer / edge checks, sample colors, and paint into target buffers."""
            nonlocal warp_depth_occluded_total, warp_depth_checked_total, warp_depth_edge_rejected_total
            if valid_indices.shape[0] == 0:
                return 0

            if do_depth_check and valid_indices.shape[0] > 0:
                depth_map_path, _ = _resolve_dense_map_path(
                    dense_path, image.name, "depth_maps", depth_map_type_warp,
                )
                if depth_map_path is not None:
                    depth_map_arr = _read_colmap_dense_array(depth_map_path)
                    if depth_map_arr.ndim == 2:
                        camera_obj = reconstruction.cameras[image.camera_id]
                        visible = sample_source_depth_visibility(
                            u_valid, v_valid, cam_z_valid,
                            depth_map_arr,
                            camera_obj.width, camera_obj.height,
                            device,
                            tolerance_m=warp_depth_tolerance_m,
                            min_depth=float(os.getenv("ORTHO_DSM_MIN_DEPTH", "0.05")),
                            neighborhood_radius_px=warp_depth_neighborhood_px,
                        )
                        n_before = valid_indices.shape[0]
                        warp_depth_checked_total += n_before
                        valid_indices = valid_indices[visible]
                        u_valid = u_valid[visible]
                        v_valid = v_valid[visible]
                        cam_z_valid = cam_z_valid[visible]
                        warp_depth_occluded_total += n_before - valid_indices.shape[0]

                        if warp_depth_edge_check and valid_indices.shape[0] > 0:
                            edge_safe = sample_source_depth_edge_mask(
                                u_valid, v_valid, depth_map_arr,
                                camera_obj.width, camera_obj.height,
                                device,
                                gradient_threshold=warp_depth_edge_gradient,
                            )
                            n_before_edge = valid_indices.shape[0]
                            valid_indices = valid_indices[edge_safe]
                            u_valid = u_valid[edge_safe]
                            v_valid = v_valid[edge_safe]
                            cam_z_valid = cam_z_valid[edge_safe]
                            warp_depth_edge_rejected_total += n_before_edge - valid_indices.shape[0]

            if valid_indices.shape[0] == 0:
                return 0

            with Image.open(img_path) as pil_img:
                img_np = np.asarray(pil_img)
                if img_np.ndim == 2:
                    img_np = np.stack([img_np] * 3, axis=-1)
                elif img_np.shape[2] == 4:
                    img_np = img_np[:, :, :3]
                tensor_img = torch.from_numpy(img_np.copy()).permute(2, 0, 1).to(device).float()

            u_norm = (u_valid / (w_img - 1)) * 2.0 - 1.0
            v_norm = (v_valid / (h_img - 1)) * 2.0 - 1.0
            u_norm = u_norm.to(dtype=tensor_img.dtype)
            v_norm = v_norm.to(dtype=tensor_img.dtype)
            grid = torch.stack([u_norm, v_norm], dim=-1).view(1, 1, -1, 2)

            sampled = F.grid_sample(
                tensor_img.unsqueeze(0), grid, mode='bilinear',
                padding_mode='zeros', align_corners=True,
            )
            colors = sampled.squeeze(0).squeeze(1)

            rows = valid_indices[:, 0]
            cols = valid_indices[:, 1]
            if enable_blending and target_blend_rgb is not None:
                if score_tensor is not None:
                    w = score_tensor[rows, cols].clamp(min=0.01).float()
                else:
                    w = torch.ones(rows.shape[0], device=device, dtype=torch.float32)
                for ch in range(3):
                    target_blend_rgb[ch].index_put_((rows, cols), colors[ch] * w, accumulate=True)
                target_blend_weight.index_put_((rows, cols), w, accumulate=True)
            else:
                target_rgb[:, rows, cols] = colors.clamp(0, 255).to(torch.uint8)
            target_painted[rows, cols] = True

            del tensor_img, grid, sampled, colors
            if device.type == 'cuda':
                torch.cuda.empty_cache()
            return int(valid_indices.shape[0])

        # ---- Process single-layer pixels (unchanged logic) ----
        projection = _project_pixels_to_image(image, single_mask, margin_px=margin_px)
        if projection["valid_indices"].shape[0] > 0 and not projection["image_missing"]:
            do_depth_check = use_depth_check if use_depth_check is not None else warp_depth_check
            n = _depth_filter_and_paint(
                projection["valid_indices"], projection["u_valid"],
                projection["v_valid"], projection["cam_z_valid"],
                image, do_depth_check, projection["img_path"],
                projection["w_img"], projection["h_img"],
                ortho_rgb, painted, blend_rgb, blend_weight, score_tensor,
            )
            total_painted += n

        # ---- Process dual-layer pixels ----
        if dual_mask is not None and int(dual_mask.sum()) > 0:
            # Project through HIGH layer
            proj_high = _project_pixels_to_image(image, dual_mask, margin_px=margin_px,
                                                  z_tensor=world_z_high)
            # Project through LOW layer
            proj_low = _project_pixels_to_image(image, dual_mask, margin_px=margin_px,
                                                 z_tensor=world_z_low)

            if (proj_high["valid_indices"].shape[0] > 0 or proj_low["valid_indices"].shape[0] > 0) and not proj_high.get("image_missing", True):
                # Load the depth map once for layer classification
                depth_map_path, _ = _resolve_dense_map_path(
                    dense_path, image.name, "depth_maps", depth_map_type_warp,
                )
                depth_map_arr = None
                if depth_map_path is not None:
                    depth_map_arr = _read_colmap_dense_array(depth_map_path)
                    if depth_map_arr.ndim != 2:
                        depth_map_arr = None

                # For pixels that project validly in BOTH layers, classify
                # which layer this camera sees using the depth map.
                # For pixels only valid in one layer, use that layer.

                # Build a combined index mapping: for each dual-mask pixel,
                # we have up to two projections. Use the mask_indices from
                # proj_high (same pixel set, same ordering).
                hi_vi = proj_high["valid_indices"]  # (N_h, 2)
                lo_vi = proj_low["valid_indices"]   # (N_l, 2)

                if hi_vi.shape[0] > 0 and lo_vi.shape[0] > 0 and depth_map_arr is not None:
                    # Find pixels valid in BOTH projections by matching indices.
                    # Convert 2D indices to flat for fast set operations.
                    hi_flat = hi_vi[:, 0] * width + hi_vi[:, 1]
                    lo_flat = lo_vi[:, 0] * width + lo_vi[:, 1]

                    # Mark which high-proj pixels also exist in low-proj
                    # and vice versa for layer classification.
                    hi_set = set(hi_flat.cpu().tolist())
                    lo_set = set(lo_flat.cpu().tolist())
                    both_set = hi_set & lo_set

                    if both_set:
                        # For these pixels: classify using depth map
                        both_tensor = torch.tensor(sorted(both_set), device=device, dtype=torch.long)
                        both_mask_2d = torch.zeros(height * width, dtype=torch.bool, device=device)
                        both_mask_2d[both_tensor] = True
                        both_mask_2d = both_mask_2d.view(height, width)

                        # Get u, v, cam_z for both layers at these pixels
                        hi_in_both = torch.tensor([f in both_set for f in hi_flat.cpu().tolist()],
                                                   dtype=torch.bool, device=device)
                        lo_in_both = torch.tensor([f in both_set for f in lo_flat.cpu().tolist()],
                                                   dtype=torch.bool, device=device)

                        u_both_hi = proj_high["u_valid"][hi_in_both]
                        v_both_hi = proj_high["v_valid"][hi_in_both]
                        cam_z_both_hi = proj_high["cam_z_valid"][hi_in_both]
                        cam_z_both_lo = proj_low["cam_z_valid"][lo_in_both]

                        camera_obj = reconstruction.cameras[image.camera_id]
                        use_high = select_layer_from_depth(
                            u_both_hi, v_both_hi,
                            cam_z_both_hi, cam_z_both_lo,
                            depth_map_arr,
                            camera_obj.width, camera_obj.height,
                            device,
                            tolerance_m=layered_layer_tolerance_m,
                            min_depth=float(os.getenv("ORTHO_DSM_MIN_DEPTH", "0.05")),
                        )

                        # Split the high-proj pixels: those classified as high stay,
                        # those classified as low get removed from high (will be handled from low).
                        # Similarly for low-proj pixels.
                        hi_vi_both = hi_vi[hi_in_both]
                        lo_vi_both = lo_vi[lo_in_both]

                        # High-only: high valid pixels NOT in both, PLUS both-pixels classified high
                        hi_only_idx = ~hi_in_both
                        hi_keep = hi_only_idx.clone()
                        hi_keep[hi_in_both] = use_high
                        lo_only_idx = ~lo_in_both
                        lo_keep = lo_only_idx.clone()
                        lo_keep[lo_in_both] = ~use_high

                        hi_vi_final = hi_vi[hi_keep]
                        hi_u_final = proj_high["u_valid"][hi_keep]
                        hi_v_final = proj_high["v_valid"][hi_keep]
                        hi_cz_final = proj_high["cam_z_valid"][hi_keep]

                        lo_vi_final = lo_vi[lo_keep]
                        lo_u_final = proj_low["u_valid"][lo_keep]
                        lo_v_final = proj_low["v_valid"][lo_keep]
                        lo_cz_final = proj_low["cam_z_valid"][lo_keep]
                    else:
                        hi_vi_final = hi_vi
                        hi_u_final = proj_high["u_valid"]
                        hi_v_final = proj_high["v_valid"]
                        hi_cz_final = proj_high["cam_z_valid"]
                        lo_vi_final = lo_vi
                        lo_u_final = proj_low["u_valid"]
                        lo_v_final = proj_low["v_valid"]
                        lo_cz_final = proj_low["cam_z_valid"]
                else:
                    hi_vi_final = hi_vi
                    hi_u_final = proj_high["u_valid"]
                    hi_v_final = proj_high["v_valid"]
                    hi_cz_final = proj_high["cam_z_valid"]
                    lo_vi_final = lo_vi
                    lo_u_final = proj_low["u_valid"]
                    lo_v_final = proj_low["v_valid"]
                    lo_cz_final = proj_low["cam_z_valid"]

                do_depth_check = use_depth_check if use_depth_check is not None else warp_depth_check

                # Paint high-layer pixels into the high ortho buffer
                if hi_vi_final.shape[0] > 0 and ortho_rgb_high is not None:
                    n_hi = _depth_filter_and_paint(
                        hi_vi_final, hi_u_final, hi_v_final, hi_cz_final,
                        image, do_depth_check, proj_high["img_path"],
                        proj_high["w_img"], proj_high["h_img"],
                        ortho_rgb_high, painted_high,
                        blend_rgb_high, blend_weight_high, score_tensor,
                    )
                    layered_warp_high_painted += n_hi
                    total_painted += n_hi

                # Paint low-layer pixels into the primary (ground) ortho buffer
                if lo_vi_final.shape[0] > 0:
                    n_lo = _depth_filter_and_paint(
                        lo_vi_final, lo_u_final, lo_v_final, lo_cz_final,
                        image, do_depth_check, proj_low["img_path"],
                        proj_low["w_img"], proj_low["h_img"],
                        ortho_rgb, painted,
                        blend_rgb, blend_weight, score_tensor,
                    )
                    layered_warp_low_painted += n_lo
                    total_painted += n_lo

        # Free VRAM
        if device.type == 'cuda':
            torch.cuda.empty_cache()

        return total_painted

    def analyze_black_pixels(black_mask, edge_fallback_enabled):
        total_black = int(black_mask.sum().item())
        analysis = {
            "total_black_pixels": total_black,
            "no_primary_candidate_pixels": int((black_mask & (voronoi_map < 0)).sum().item()),
            "no_fallback_candidate_pixels": int((black_mask & (fallback_voronoi_map < 0)).sum().item()),
            "fallback_assigned_black_pixels": int((black_mask & (fallback_voronoi_map >= 0)).sum().item()),
            "fallback_failure_counts": {
                "image_missing": 0,
                "behind_camera": 0,
                "out_of_bounds": 0,
                "skipped_edge_policy": 0,
                "unknown": 0,
            },
            "fallback_failure_by_image": [],
        }
        if total_black == 0:
            return analysis

        per_image_failures = {}
        assigned_ids = torch.unique(fallback_voronoi_map[black_mask])
        for img_id_tensor in assigned_ids:
            img_id = int(img_id_tensor.item())
            if img_id < 0:
                continue
            image = reconstruction.images[img_id]
            mask = black_mask & (fallback_voronoi_map == img_id)
            projection = _project_pixels_to_image(image, mask, margin_px=fallback_margin_px)
            requested = int(mask.sum().item())
            if requested == 0:
                continue

            image_failure = {
                "image_id": img_id,
                "image_name": image.name,
                "requested_black_pixels": requested,
                "image_missing": 0,
                "behind_camera": 0,
                "out_of_bounds": 0,
                "skipped_edge_policy": 0,
                "unknown": 0,
            }

            if projection["image_missing"]:
                image_failure["image_missing"] = requested
                analysis["fallback_failure_counts"]["image_missing"] += requested
            else:
                if not edge_fallback_enabled:
                    skipped_edge_policy = int((mask & edge_assignment_mask).sum().item())
                    if skipped_edge_policy > 0:
                        image_failure["skipped_edge_policy"] = skipped_edge_policy
                        analysis["fallback_failure_counts"]["skipped_edge_policy"] += skipped_edge_policy

                front_count = int(projection["front_mask"].sum().item())
                behind_count = requested - front_count
                if behind_count > 0:
                    image_failure["behind_camera"] = behind_count
                    analysis["fallback_failure_counts"]["behind_camera"] += behind_count

                if front_count > 0:
                    in_bounds_count = int(projection["in_bounds_mask"].sum().item())
                    out_of_bounds_count = front_count - in_bounds_count
                    if out_of_bounds_count > 0:
                        image_failure["out_of_bounds"] = out_of_bounds_count
                        analysis["fallback_failure_counts"]["out_of_bounds"] += out_of_bounds_count

                classified = (
                    image_failure["image_missing"]
                    + image_failure["behind_camera"]
                    + image_failure["out_of_bounds"]
                    + image_failure["skipped_edge_policy"]
                )
                unknown = max(0, requested - classified)
                if unknown > 0:
                    image_failure["unknown"] = unknown
                    analysis["fallback_failure_counts"]["unknown"] += unknown

            per_image_failures[img_id] = image_failure

        analysis["fallback_failure_by_image"] = sorted(
            per_image_failures.values(),
            key=lambda entry: entry["requested_black_pixels"],
            reverse=True,
        )[:20]
        return analysis

    # ---- Pass 1: angle-aware assignment ----
    # When blending is enabled, every primary camera paints all pixels where
    # it scores above threshold (not just its "winning" pixels). The per-pixel
    # camera score is used as a blending weight so the final colour is a
    # weighted average of all contributing cameras — this averages out
    # per-camera sampling noise and eliminates seam artifacts.
    report(vol_id, "ORTHO", 97, f"Pass 1: angle-aware warping {total_images} images (blending={'on' if enable_blending else 'off'})…", report_fn)
    images_processed = 0
    total_painted = 0

    for img_id, image in reconstruction.images.items():
        if enable_blending and img_id in valid_images:
            # Let every primary camera paint all pixels it scores above threshold
            cam_assigned = (voronoi_map == img_id) & valid_dsm_mask
            if not cam_assigned.any():
                image_stats[img_id]["primary_painted_pixels"] = 0
                images_processed += 1
                continue
            mask = cam_assigned  # still use winner-takes-all for mask selection
            n_painted = _warp_image(img_id, image, mask, margin_px=primary_margin_px,
                                   score_tensor=primary_best_score.float())
        else:
            mask = (voronoi_map == img_id) & valid_dsm_mask
            n_painted = _warp_image(img_id, image, mask, margin_px=primary_margin_px)
        total_painted += n_painted
        image_stats[img_id]["primary_painted_pixels"] = int(n_painted)

        images_processed += 1
        if images_processed % 50 == 0 or images_processed == total_images:
            pct = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
            report(vol_id, "ORTHO", 97,
                   f"Pass 1: {images_processed}/{total_images} images, {pct:.1f}% filled…", report_fn)

    pass1_fill = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
    report(vol_id, "ORTHO", 97, f"Pass 1 done: {pass1_fill:.1f}% filled.", report_fn)

    # ---- Pass 2: Per-pixel scored fallback fill ----
    # Use a second visibility map with looser thresholds so each remaining hole
    # is attempted from the best fallback camera for that pixel, rather than a
    # coarse image-by-image heuristic.
    enable_fallback_fill = str(os.getenv("ORTHO_DSM_ENABLE_FALLBACK_FILL", "1")).strip().lower() in ("1", "true", "yes", "on")
    enable_edge_fallback_fill = str(os.getenv("ORTHO_DSM_ENABLE_EDGE_FALLBACK_FILL", "1")).strip().lower() in ("1", "true", "yes", "on")
    unpainted = valid_dsm_mask & (~painted)
    n_unpainted = int(unpainted.sum())
    edge_fallback_painted_pixels = 0
    non_edge_fallback_painted_pixels = 0

    if enable_fallback_fill and n_unpainted > 0:
        report(vol_id, "ORTHO", 97, f"Pass 2: Filling {n_unpainted} remaining pixels…", report_fn)
        fallback_processed = 0
        for img_id in fallback_images:
            image = reconstruction.images[img_id]
            mask = (fallback_voronoi_map == img_id) & valid_dsm_mask & (~painted)
            if not enable_edge_fallback_fill:
                mask = mask & (~edge_assignment_mask)
            if not mask.any():
                continue
            edge_mask = mask & edge_assignment_mask
            non_edge_mask = mask & (~edge_assignment_mask)
            n_painted = _warp_image(img_id, image, mask, margin_px=fallback_margin_px, use_depth_check=warp_depth_check_fallback)
            image_stats[img_id]["fallback_painted_pixels"] = int(n_painted)
            if edge_mask.any():
                edge_fallback_painted_pixels += int((painted & edge_mask).sum().item())
            if non_edge_mask.any():
                non_edge_fallback_painted_pixels += int((painted & non_edge_mask).sum().item())
            fallback_processed += 1
            if fallback_processed % 25 == 0:
                remaining = int((valid_dsm_mask & (~painted)).sum())
                report(
                    vol_id,
                    "ORTHO",
                    97,
                    f"Pass 2: tried {fallback_processed}/{len(fallback_images)} scored fallback images, {remaining} pixels left…",
                    report_fn,
                )
    elif n_unpainted > 0:
        report(vol_id, "ORTHO", 97, f"Skipping fallback fill for {n_unpainted} pixels to avoid edge artifacts.", report_fn)

    # ---- Pass 2b: Unscored hole-filling sweep ----
    # After the scored passes, there may still be holes where the assigned
    # camera was Z-buffer blocked. Sweep ALL cameras (sorted by nadirness)
    # and try to paint every remaining hole. The Z-buffer ensures only
    # non-occluded projections succeed.
    remaining_after_fallback = int((valid_dsm_mask & (~painted)).sum())
    enable_hole_sweep = str(os.getenv("ORTHO_DSM_ENABLE_HOLE_SWEEP", "1")).strip().lower() in ("1", "true", "yes", "on")
    hole_sweep_painted = 0
    if enable_hole_sweep and remaining_after_fallback > 0:
        # Sort all cameras by nadirness (most nadir first) for quality
        sorted_img_ids = sorted(
            reconstruction.images.keys(),
            key=lambda iid: build_diagnostics["image_angles_deg"].get(iid, 90.0),
        )
        report(
            vol_id, "ORTHO", 97,
            f"Pass 2b: hole-filling sweep with {len(sorted_img_ids)} cameras for {remaining_after_fallback} remaining pixels…",
            report_fn,
        )
        sweep_processed = 0
        for img_id in sorted_img_ids:
            remaining_mask = valid_dsm_mask & (~painted)
            if not remaining_mask.any():
                break
            image = reconstruction.images[img_id]
            n_painted = _warp_image(
                img_id, image, remaining_mask,
                margin_px=fallback_margin_px,
                use_depth_check=True,
            )
            hole_sweep_painted += n_painted
            sweep_processed += 1
            if sweep_processed % 25 == 0:
                remaining = int((valid_dsm_mask & (~painted)).sum())
                report(
                    vol_id, "ORTHO", 97,
                    f"Pass 2b: tried {sweep_processed}/{len(sorted_img_ids)} cameras, {remaining} pixels left…",
                    report_fn,
                )
        remaining_after_sweep = int((valid_dsm_mask & (~painted)).sum())
        report(
            vol_id, "ORTHO", 97,
            f"Pass 2b done: painted {hole_sweep_painted}, {remaining_after_sweep} pixels left.",
            report_fn,
        )

    # ---- Resolve blended colours ----
    # If multi-camera blending was active, convert accumulated weighted sums
    # into final pixel colours.
    if enable_blending:
        has_blend = blend_weight > 0
        for ch in range(3):
            blend_rgb[ch][has_blend] /= blend_weight[has_blend]
        ortho_rgb = blend_rgb.clamp(0, 255).to(torch.uint8)
        blend_pixels = int(has_blend.sum().item())
        report(vol_id, "ORTHO", 98, f"Blend resolve: {blend_pixels} pixels from weighted multi-camera average.", report_fn)
        del blend_rgb, blend_weight

        # Resolve high-layer blending
        if layered_dsm_active and blend_rgb_high is not None and blend_weight_high is not None:
            has_blend_high = blend_weight_high > 0
            for ch in range(3):
                blend_rgb_high[ch][has_blend_high] /= blend_weight_high[has_blend_high]
            ortho_rgb_high = blend_rgb_high.clamp(0, 255).to(torch.uint8)
            hi_blend_count = int(has_blend_high.sum().item())
            report(vol_id, "ORTHO", 98, f"High-layer blend resolve: {hi_blend_count} pixels.", report_fn)
            del blend_rgb_high, blend_weight_high
    else:
        blend_pixels = 0

    # ── Merge dual-layer ortho: composite high (object) on top of low (ground) ──
    # The high layer represents the object surface (car roof, etc.) and should
    # be painted on top of the ground layer at dual-layer cells.  This gives
    # us clean ground texture right up to the object boundary AND a crisp
    # object top, eliminating the halo artifact.
    if layered_dsm_active and ortho_rgb_high is not None and painted_high is not None:
        composite_mask = has_dual_layer & painted_high
        composite_count = int(composite_mask.sum().item())
        if composite_count > 0:
            ortho_rgb[:, composite_mask] = ortho_rgb_high[:, composite_mask]
            painted[composite_mask] = True
        report(
            vol_id, "ORTHO", 98,
            f"Layer merge: composited {composite_count} high-layer pixels on top of ground. "
            f"(high painted={layered_warp_high_painted}, low painted={layered_warp_low_painted})",
            report_fn,
        )
        del ortho_rgb_high, painted_high

    # ---- Pass 3: Small-hole inpainting ----
    # If the ground is still occluded in every viable image, no projection can
    # recover the true texture. In that case, fill only small remaining holes
    # from surrounding painted pixels to avoid black seams around objects.
    remaining_holes = int((valid_dsm_mask & (~painted)).sum())
    enable_inpaint_fill = str(os.getenv("ORTHO_DSM_ENABLE_INPAINT", "1")).strip().lower() in ("1", "true", "yes", "on")
    if enable_inpaint_fill and remaining_holes > 0:
        edge_inpaint_dilation_px = max(0, int(os.getenv("ORTHO_DSM_INPAINT_EDGE_DILATION_PX", "2")))
        inpaint_edge_only = str(os.getenv("ORTHO_DSM_INPAINT_EDGE_ONLY", "0")).strip().lower() in ("1", "true", "yes", "on")
        inpaint_min_valid_neighbors = max(0, int(os.getenv("ORTHO_DSM_INPAINT_MIN_VALID_NEIGHBORS", "3")))
        inpaint_exclude_edge_assignment = str(os.getenv("ORTHO_DSM_INPAINT_EXCLUDE_EDGE_ASSIGNMENT", "0")).strip().lower() in ("1", "true", "yes", "on")
        inpaint_interior_radius_px = max(0, int(os.getenv("ORTHO_DSM_INPAINT_INTERIOR_RADIUS_PX", "0")))
        inpaint_allowed_mask = valid_dsm_mask
        if inpaint_edge_only:
            inpaint_zone = edge_sensitive_mask
            if edge_inpaint_dilation_px > 0 and edge_sensitive_mask.any():
                kernel_size = edge_inpaint_dilation_px * 2 + 1
                inpaint_zone = F.max_pool2d(
                    edge_sensitive_mask.float().unsqueeze(0).unsqueeze(0),
                    kernel_size=kernel_size,
                    stride=1,
                    padding=edge_inpaint_dilation_px,
                ).squeeze(0).squeeze(0) > 0
            inpaint_allowed_mask = valid_dsm_mask & inpaint_zone
        if inpaint_exclude_edge_assignment:
            inpaint_allowed_mask = inpaint_allowed_mask & (~edge_assignment_mask)
        if inpaint_interior_radius_px > 0:
            interior_kernel_size = inpaint_interior_radius_px * 2 + 1
            valid_window_count = F.avg_pool2d(
                valid_dsm_mask.float().unsqueeze(0).unsqueeze(0),
                kernel_size=interior_kernel_size,
                stride=1,
                padding=inpaint_interior_radius_px,
                divisor_override=1,
            ).squeeze(0).squeeze(0)
            inpaint_allowed_mask = inpaint_allowed_mask & (
                valid_window_count >= float(interior_kernel_size * interior_kernel_size)
            )
        if inpaint_min_valid_neighbors > 0:
            valid_neighbor_count = F.avg_pool2d(
                valid_dsm_mask.float().unsqueeze(0).unsqueeze(0),
                kernel_size=3,
                stride=1,
                padding=1,
                divisor_override=1,
            ).squeeze(0).squeeze(0)
            inpaint_allowed_mask = inpaint_allowed_mask & (valid_neighbor_count >= float(inpaint_min_valid_neighbors))
        ortho_rgb, painted = fill_small_color_holes_gpu(
            ortho_rgb,
            painted,
            inpaint_allowed_mask,
            iterations=int(os.getenv("ORTHO_DSM_INPAINT_ITERATIONS", "60")),
            min_neighbors=int(os.getenv("ORTHO_DSM_INPAINT_MIN_NEIGHBORS", "2")),
        )
        filled_after_inpaint = int((valid_dsm_mask & painted).sum())
        report(vol_id, "ORTHO", 98, f"Pass 3: inpainted small holes, coverage now {100.0 * filled_after_inpaint / max(1, valid_dsm_mask.sum().item()):.1f}%.", report_fn)
    elif remaining_holes > 0:
        report(vol_id, "ORTHO", 98, f"Skipping synthetic color inpainting for {remaining_holes} remaining pixels to avoid out-of-context colors.", report_fn)

    final_fill = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
    fallback_success = sum(stats["fallback_painted_pixels"] for stats in image_stats.values())
    black_pixel_analysis = analyze_black_pixels(valid_dsm_mask & (~painted), enable_edge_fallback_fill)
    report(
        vol_id,
        "ORTHO",
        98,
        f"Diagnostics: primary painted={sum(stats['primary_painted_pixels'] for stats in image_stats.values())}, fallback painted={fallback_success}, sweep painted={hole_sweep_painted}, remaining holes={int((valid_dsm_mask & (~painted)).sum())}",
        report_fn,
    )
    if black_pixel_analysis["total_black_pixels"] > 0:
        report(
            vol_id,
            "ORTHO",
            98,
            (
                "Black-pixel analysis: "
                f"no_primary_candidate={black_pixel_analysis['no_primary_candidate_pixels']}, "
                f"no_fallback_candidate={black_pixel_analysis['no_fallback_candidate_pixels']}, "
                f"behind_camera={black_pixel_analysis['fallback_failure_counts']['behind_camera']}, "
                f"out_of_bounds={black_pixel_analysis['fallback_failure_counts']['out_of_bounds']}, "
                f"skipped_edge_policy={black_pixel_analysis['fallback_failure_counts']['skipped_edge_policy']}, "
                f"image_missing={black_pixel_analysis['fallback_failure_counts']['image_missing']}, "
                f"unknown={black_pixel_analysis['fallback_failure_counts']['unknown']}"
            ),
            report_fn,
        )
    report(vol_id, "ORTHO", 98, f"Mosaicking complete: {final_fill:.1f}% fill rate.", report_fn)
    if warp_depth_check and warp_depth_checked_total > 0:
        report(
            vol_id, "ORTHO", 98,
            f"Z-buffer depth check: {warp_depth_checked_total} checked, "
            f"{warp_depth_occluded_total} occluded ({100.0 * warp_depth_occluded_total / warp_depth_checked_total:.1f}%), "
            f"depth-edge rejected={warp_depth_edge_rejected_total}",
            report_fn,
        )

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

    diagnostics_payload = {
        "resolution": float(resolution),
        "width": int(width),
        "height": int(height),
        "raw_valid_pixels": raw_valid_count,
        "filled_pixels_before_support_limit": filled_dsm_count,
        "valid_pixels": int(valid_dsm_mask.sum().item()),
        "gap_filled_added_pixels": int(filled_dsm_count - raw_valid_mask.sum().item()),
        "rejected_far_fill_pixels": int(filled_dsm_count - valid_dsm_mask.sum().item()),
        "support_distance_limit_px": build_diagnostics["max_support_distance_px"],
        "fill_iterations": build_diagnostics["fill_iterations"],
        "depth_map_type": build_diagnostics["depth_map_type"],
        "depth_stride": build_diagnostics["depth_stride"],
        "depth_maps_found": build_diagnostics["depth_maps_found"],
        "depth_maps_used": build_diagnostics["depth_maps_used"],
        "normal_maps_used": build_diagnostics["normal_maps_used"],
        "depth_samples_considered": build_diagnostics["depth_samples_considered"],
        "depth_samples_accepted": build_diagnostics["depth_samples_accepted"],
        "dsm_build_max_view_angle_deg": build_diagnostics["dsm_build_max_view_angle_deg"],
        "dsm_source_image_count": build_diagnostics["dsm_source_image_count"],
        "dsm_source_images_skipped_by_angle_count": build_diagnostics["dsm_source_images_skipped_by_angle_count"],
        "dsm_ambiguous_height_m": build_diagnostics.get("dsm_ambiguous_height_m"),
        "dsm_ambiguous_fixed_count": build_diagnostics.get("dsm_ambiguous_fixed_count", 0),
        "dsm_anomaly_window": build_diagnostics.get("dsm_anomaly_window"),
        "dsm_anomaly_threshold_m": build_diagnostics.get("dsm_anomaly_threshold_m"),
        "dsm_anomaly_ratio": build_diagnostics.get("dsm_anomaly_ratio"),
        "dsm_anomaly_fixed_count": build_diagnostics.get("dsm_anomaly_fixed_count", 0),
        "support_distance_histogram": build_diagnostics["support_distance_histogram"],
        "final_fill_percent": float(final_fill),
        "remaining_holes": int((valid_dsm_mask & (~painted)).sum().item()),
        "black_pixel_analysis": black_pixel_analysis,
        "edge_depth_range_m": build_diagnostics["edge_depth_range_m"],
        "edge_min_raw_support": build_diagnostics["edge_min_raw_support"],
        "edge_dilation_px": build_diagnostics["edge_dilation_px"],
        "edge_assignment_dilation_px": build_diagnostics["edge_assignment_dilation_px"],
        "edge_occlusion_buffer_px": build_diagnostics["edge_occlusion_buffer_px"],
        "raw_edge_pixel_count": build_diagnostics["raw_edge_pixel_count"],
        "edge_sensitive_pixel_count": build_diagnostics["edge_sensitive_pixel_count"],
        "edge_assignment_pixel_count": build_diagnostics["edge_assignment_pixel_count"],
        "edge_occlusion_pixel_count": build_diagnostics["edge_occlusion_pixel_count"],
        "edge_rejected_count": build_diagnostics["edge_rejected_count"],
        "edge_source_depth_tolerance_m": build_diagnostics["edge_source_depth_tolerance_m"],
        "edge_source_depth_neighborhood_px": build_diagnostics["edge_source_depth_neighborhood_px"],
        "edge_assignment_max_support_distance_px": build_diagnostics["edge_assignment_max_support_distance_px"],
        "edge_raw_depth_spread_m": build_diagnostics["edge_raw_depth_spread_m"],
        "edge_raw_depth_spread_min_samples": build_diagnostics["edge_raw_depth_spread_min_samples"],
        "edge_source_visibility_checked_count": build_diagnostics["edge_source_visibility_checked_count"],
        "edge_source_visibility_rejected_count": build_diagnostics["edge_source_visibility_rejected_count"],
        "edge_assignment_paint_rejected_count": build_diagnostics["edge_assignment_paint_rejected_count"],
        "edge_raw_mixed_depth_rejected_count": build_diagnostics["edge_raw_mixed_depth_rejected_count"],
        "warp_depth_check": warp_depth_check,
        "warp_depth_check_fallback": warp_depth_check_fallback,
        "warp_depth_tolerance_m": warp_depth_tolerance_m,
        "warp_depth_neighborhood_px": warp_depth_neighborhood_px,
        "warp_depth_checked_total": warp_depth_checked_total,
        "warp_depth_occluded_total": warp_depth_occluded_total,
        "warp_depth_edge_check": warp_depth_edge_check,
        "warp_depth_edge_gradient": warp_depth_edge_gradient,
        "warp_depth_edge_rejected_total": warp_depth_edge_rejected_total,
        "blending_enabled": enable_blending,
        "layered_dsm_enabled": build_diagnostics.get("layered_dsm_enabled", False),
        "layered_dsm_threshold_m": build_diagnostics.get("layered_dsm_threshold_m"),
        "layered_dilation_px": build_diagnostics.get("layered_dilation_px", 0),
        "dual_layer_pixel_count": build_diagnostics.get("dual_layer_pixel_count", 0),
        "layered_warp_high_painted": layered_warp_high_painted,
        "layered_warp_low_painted": layered_warp_low_painted,
        "layered_layer_tolerance_m": layered_layer_tolerance_m,
        "dsm_morph_opening_px": build_diagnostics.get("dsm_morph_opening_px", 0),
        "dsm_morph_fixed_count": build_diagnostics.get("dsm_morph_fixed_count", 0),
        "edge_fallback_fill_enabled": enable_edge_fallback_fill,
        "edge_fallback_painted_pixels": int(edge_fallback_painted_pixels),
        "non_edge_fallback_painted_pixels": int(non_edge_fallback_painted_pixels),
        "hole_sweep_painted_pixels": int(hole_sweep_painted),
        "edge_primary_max_view_angle_deg": build_diagnostics["edge_primary_max_view_angle_deg"],
        "edge_fallback_max_view_angle_deg": build_diagnostics["edge_fallback_max_view_angle_deg"],
        "primary_image_count": len(valid_images),
        "fallback_image_count": len(fallback_images),
        "thresholds": {
            "primary": build_diagnostics["primary_thresholds"],
            "fallback": build_diagnostics["fallback_thresholds"],
            "primary_margin_px": int(primary_margin_px),
            "fallback_margin_px": int(fallback_margin_px),
            "inpaint_min_valid_neighbors": int(os.getenv("ORTHO_DSM_INPAINT_MIN_VALID_NEIGHBORS", "3")),
            "inpaint_exclude_edge_assignment": str(os.getenv("ORTHO_DSM_INPAINT_EXCLUDE_EDGE_ASSIGNMENT", "0")).strip().lower() in ("1", "true", "yes", "on"),
            "inpaint_interior_radius_px": max(0, int(os.getenv("ORTHO_DSM_INPAINT_INTERIOR_RADIUS_PX", "0"))),
        },
        "images": [
            {
                "image_id": int(img_id),
                **image_stats[img_id],
            }
            for img_id in sorted(image_stats.keys())
        ],
    }
    try:
        with open(diagnostics_path, "w", encoding="utf-8") as handle:
            json.dump(diagnostics_payload, handle, indent=2)
        report(vol_id, "ORTHO", 99, f"Ortho diagnostics written to {diagnostics_path}", report_fn)
    except OSError as error:
        report(vol_id, "ORTHO", 99, f"Failed to write ortho diagnostics: {error}", report_fn)

    report(vol_id, "ORTHO", 99, f"True Orthophoto written: {width}×{height} px @ {resolution} m/px ({final_fill:.1f}% coverage)", report_fn)
