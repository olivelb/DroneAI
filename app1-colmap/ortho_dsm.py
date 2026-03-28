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
    return valid_sampled_depth & (cam_depth <= (sampled_depth + float(tolerance_m)))


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

    for image in reconstruction.images.values():
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
            sample_count_flat.scatter_add_(0, idx_t, torch.ones_like(z_t))

            accepted_count = int(flat_idx.size)
            depth_samples_accepted += accepted_count
            image_used = True

        if image_used:
            depth_maps_used += 1

    dsm = dsm_flat.view(height, width)
    raw_dsm = dsm.clone()
    sample_count = sample_count_flat.view(height, width)

    # Track which pixels had ACTUAL depth-map support BEFORE gap filling.
    raw_valid = sample_count > 0
    if not raw_valid.any():
        raise RuntimeError(
            f"No valid DSM cells could be accumulated from COLMAP depth maps in {os.path.join(dense_path, 'stereo', 'depth_maps')}"
        )

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
    max_support_distance_px = float(os.getenv("ORTHO_DSM_MAX_SUPPORT_DISTANCE_PX", "3"))
    if max_support_distance_px < 0:
        valid_mask = filled_valid
    else:
        valid_mask = filled_valid & (support_distance_px <= max_support_distance_px)

    edge_depth_range_m = float(os.getenv("ORTHO_DSM_EDGE_DEPTH_RANGE_M", "0.20"))
    edge_min_raw_support = float(os.getenv("ORTHO_DSM_EDGE_MIN_RAW_SUPPORT", "4"))
    edge_dilation_px = max(0, int(os.getenv("ORTHO_DSM_EDGE_DILATION_PX", "1")))
    edge_assignment_dilation_px = max(edge_dilation_px, int(os.getenv("ORTHO_DSM_EDGE_ASSIGNMENT_DILATION_PX", "3")))
    edge_source_depth_tolerance_m = float(os.getenv("ORTHO_DSM_EDGE_SOURCE_DEPTH_TOLERANCE_M", "0.08"))
    edge_source_depth_neighborhood_px = max(0, int(os.getenv("ORTHO_DSM_EDGE_SOURCE_DEPTH_NEIGHBORHOOD_PX", "1")))
    edge_primary_max_view_angle_deg = float(os.getenv("ORTHO_DSM_EDGE_PRIMARY_MAX_VIEW_ANGLE_DEG", str(NADIR_THRESHOLD_DEG)))
    edge_fallback_max_view_angle_deg = float(os.getenv("ORTHO_DSM_EDGE_FALLBACK_MAX_VIEW_ANGLE_DEG", str(NADIR_THRESHOLD_DEG)))
    raw_edge_mask = torch.zeros_like(raw_valid)
    edge_sensitive_mask = torch.zeros_like(raw_valid)
    edge_assignment_mask = torch.zeros_like(raw_valid)
    edge_rejected_mask = torch.zeros_like(raw_valid)
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
        edge_rejected_mask = boundary_neighborhood & (
            (~raw_valid)
            | ((sample_count < edge_min_raw_support) & raw_valid)
        )
        valid_mask = valid_mask & (~edge_rejected_mask)
    else:
        edge_assignment_mask = edge_sensitive_mask

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
    edge_primary_incidence_min = float(os.getenv("ORTHO_DSM_EDGE_PRIMARY_INCIDENCE_MIN", "0.35"))
    edge_primary_axis_alignment_min = float(os.getenv("ORTHO_DSM_EDGE_PRIMARY_AXIS_ALIGNMENT_MIN", "0.50"))
    edge_primary_nadirness_min = float(os.getenv("ORTHO_DSM_EDGE_PRIMARY_NADIRNESS_MIN", "0.82"))
    allow_fallback_on_edges = str(os.getenv("ORTHO_DSM_ALLOW_FALLBACK_ON_EDGES", "1")).strip().lower() in ("1", "true", "yes", "on")
    edge_fallback_incidence_min = float(os.getenv("ORTHO_DSM_EDGE_FALLBACK_INCIDENCE_MIN", "0.25"))
    edge_fallback_axis_alignment_min = float(os.getenv("ORTHO_DSM_EDGE_FALLBACK_AXIS_ALIGNMENT_MIN", "0.40"))
    edge_fallback_nadirness_min = float(os.getenv("ORTHO_DSM_EDGE_FALLBACK_NADIRNESS_MIN", "0.82"))

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

        edge_angle_ok = True
        if image_angles_deg.get(img_id) is not None:
            edge_angle_ok = image_angles_deg[img_id] <= edge_primary_max_view_angle_deg
        edge_fallback_angle_ok = True
        if image_angles_deg.get(img_id) is not None:
            edge_fallback_angle_ok = image_angles_deg[img_id] <= edge_fallback_max_view_angle_deg

        source_visible_on_edges = torch.zeros_like(valid_mask, dtype=torch.bool)
        if edge_assignment_mask.any() and edge_source_depth_tolerance_m >= 0.0:
            edge_projection_mask = projection_in_bounds & edge_assignment_mask
            edge_projection_count = int(edge_projection_mask.sum().item())
            if edge_projection_count > 0:
                edge_source_visibility_checked_count += edge_projection_count
                depth_map_path, _ = _resolve_dense_map_path(
                    dense_path,
                    image.name,
                    "depth_maps",
                    depth_map_type,
                )
                if depth_map_path is not None:
                    depth_map = _read_colmap_dense_array(depth_map_path)
                    if depth_map.ndim == 2:
                        visible_edge_samples = sample_source_depth_visibility(
                            u[edge_projection_mask],
                            v[edge_projection_mask],
                            cam_z[edge_projection_mask],
                            depth_map,
                            camera.width,
                            camera.height,
                            device,
                            tolerance_m=edge_source_depth_tolerance_m,
                            min_depth=min_depth,
                            neighborhood_radius_px=edge_source_depth_neighborhood_px,
                        )
                        source_visible_on_edges[edge_projection_mask] = visible_edge_samples
                edge_source_visibility_rejected_count += edge_projection_count - int(source_visible_on_edges[edge_projection_mask].sum().item())

        projection_allowed = projection_in_bounds & ((~edge_assignment_mask) | source_visible_on_edges)

        primary_edge_guard = (
            (~edge_assignment_mask)
            | (
                bool(edge_angle_ok)
                &
                (incidence > edge_primary_incidence_min)
                & (axis_alignment > edge_primary_axis_alignment_min)
                & (nadirness > edge_primary_nadirness_min)
            )
        )
        fallback_edge_guard = (
            (~edge_assignment_mask)
            | (
                allow_fallback_on_edges
                & edge_fallback_angle_ok
                & (incidence > edge_fallback_incidence_min)
                & (axis_alignment > edge_fallback_axis_alignment_min)
                & (nadirness > edge_fallback_nadirness_min)
            )
        )

        if img_id in valid_images:
            primary_score = torch.where(
                (incidence > primary_incidence_min)
                & (axis_alignment > primary_axis_alignment_min)
                & (nadirness > primary_nadirness_min)
                & primary_edge_guard
                & projection_allowed
                & valid_mask,
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
                & fallback_edge_guard
                & projection_allowed
                & valid_mask,
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
        "raw_edge_pixel_count": int(raw_edge_mask.sum().item()),
        "edge_sensitive_pixel_count": int(edge_sensitive_mask.sum().item()),
        "edge_assignment_pixel_count": int(edge_assignment_mask.sum().item()),
        "edge_rejected_count": int(edge_rejected_mask.sum().item()),
        "edge_source_depth_tolerance_m": edge_source_depth_tolerance_m,
        "edge_source_depth_neighborhood_px": edge_source_depth_neighborhood_px,
        "edge_source_visibility_checked_count": edge_source_visibility_checked_count,
        "edge_source_visibility_rejected_count": edge_source_visibility_rejected_count,
        "edge_primary_thresholds": {
            "incidence_min": edge_primary_incidence_min,
            "axis_alignment_min": edge_primary_axis_alignment_min,
            "nadirness_min": edge_primary_nadirness_min,
            "max_view_angle_deg": edge_primary_max_view_angle_deg,
        },
        "edge_fallback_thresholds": {
            "allow_on_edges": allow_fallback_on_edges,
            "incidence_min": edge_fallback_incidence_min,
            "axis_alignment_min": edge_fallback_axis_alignment_min,
            "nadirness_min": edge_fallback_nadirness_min,
            "max_view_angle_deg": edge_fallback_max_view_angle_deg,
        },
        "max_support_distance_px": max_support_distance_px,
        "fill_iterations": fill_iterations,
        "depth_map_type": depth_map_type,
        "depth_stride": depth_stride,
        "depth_maps_found": depth_maps_found,
        "depth_maps_used": depth_maps_used,
        "normal_maps_used": normal_maps_used,
        "depth_samples_considered": depth_samples_considered,
        "depth_samples_accepted": depth_samples_accepted,
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

    dsm, raw_valid_mask, support_distance_px, voronoi_map, fallback_voronoi_map, valid_dsm_mask, edge_sensitive_mask, edge_assignment_mask, min_x, max_y, width, height, valid_images, fallback_images, build_diagnostics = build_dsm_and_voronoi(
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

    def _project_pixels_to_image(image, pixel_mask, margin_px=None):
        """Project DSM pixels into an image and return projection diagnostics."""
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
                "w_img": 0,
                "h_img": 0,
                "img_path": None,
                "image_missing": False,
            }

        pts_local = geo_to_local(world_x[pixel_mask], world_y[pixel_mask], world_z[pixel_mask])
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
                "w_img": int(camera.width),
                "h_img": int(camera.height),
                "img_path": os.path.join(images_dir, image.name),
                "image_missing": not os.path.exists(os.path.join(images_dir, image.name)),
            }

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
            "w_img": w_img,
            "h_img": h_img,
            "img_path": img_path,
            "image_missing": not os.path.exists(img_path),
        }

    def _warp_image(img_id, image, pixel_mask, margin_px=None):
        """Warp one image onto the DSM for pixels in pixel_mask.
        Returns the number of successfully painted pixels."""
        projection = _project_pixels_to_image(image, pixel_mask, margin_px=margin_px)
        valid_indices = projection["valid_indices"]
        if valid_indices.shape[0] == 0 or projection["image_missing"]:
            return 0

        with Image.open(projection["img_path"]) as pil_img:
            img_np = np.asarray(pil_img)
            if img_np.ndim == 2:
                img_np = np.stack([img_np] * 3, axis=-1)
            elif img_np.shape[2] == 4:
                img_np = img_np[:, :, :3]
            tensor_img = torch.from_numpy(img_np.copy()).permute(2, 0, 1).to(device).float()

        # Bilinear sample
        u_norm = (projection["u_valid"] / (projection["w_img"] - 1)) * 2.0 - 1.0
        v_norm = (projection["v_valid"] / (projection["h_img"] - 1)) * 2.0 - 1.0
        u_norm = u_norm.to(dtype=tensor_img.dtype)
        v_norm = v_norm.to(dtype=tensor_img.dtype)
        grid = torch.stack([u_norm, v_norm], dim=-1).view(1, 1, -1, 2)

        sampled = F.grid_sample(
            tensor_img.unsqueeze(0), grid, mode='bilinear',
            padding_mode='zeros', align_corners=True,
        )
        colors = sampled.squeeze(0).squeeze(1)  # (3, N)

        ortho_rgb[:, valid_indices[:, 0], valid_indices[:, 1]] = colors.clamp(0, 255).to(torch.uint8)
        painted[valid_indices[:, 0], valid_indices[:, 1]] = True

        # Free VRAM
        del tensor_img, grid, sampled, colors
        if device.type == 'cuda':
            torch.cuda.empty_cache()

        return int(valid_indices.shape[0])

    def analyze_black_pixels(black_mask):
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
                "unknown": 0,
            }

            if projection["image_missing"]:
                image_failure["image_missing"] = requested
                analysis["fallback_failure_counts"]["image_missing"] += requested
            else:
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
    report(vol_id, "ORTHO", 97, f"Pass 1: angle-aware warping {total_images} images…", report_fn)
    images_processed = 0
    total_painted = 0

    for img_id, image in reconstruction.images.items():
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
    enable_edge_fallback_fill = str(os.getenv("ORTHO_DSM_ENABLE_EDGE_FALLBACK_FILL", "0")).strip().lower() in ("1", "true", "yes", "on")
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
            n_painted = _warp_image(img_id, image, mask, margin_px=fallback_margin_px)
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

    # ---- Pass 3: Small-hole inpainting ----
    # If the ground is still occluded in every viable image, no projection can
    # recover the true texture. In that case, fill only small remaining holes
    # from surrounding painted pixels to avoid black seams around objects.
    remaining_holes = int((valid_dsm_mask & (~painted)).sum())
    enable_inpaint_fill = str(os.getenv("ORTHO_DSM_ENABLE_INPAINT", "1")).strip().lower() in ("1", "true", "yes", "on")
    if enable_inpaint_fill and remaining_holes > 0:
        edge_inpaint_dilation_px = max(0, int(os.getenv("ORTHO_DSM_INPAINT_EDGE_DILATION_PX", "2")))
        inpaint_edge_only = str(os.getenv("ORTHO_DSM_INPAINT_EDGE_ONLY", "1")).strip().lower() in ("1", "true", "yes", "on")
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
        ortho_rgb, painted = fill_small_color_holes_gpu(
            ortho_rgb,
            painted,
            inpaint_allowed_mask,
            iterations=int(os.getenv("ORTHO_DSM_INPAINT_ITERATIONS", "4")),
            min_neighbors=int(os.getenv("ORTHO_DSM_INPAINT_MIN_NEIGHBORS", "4")),
        )
        filled_after_inpaint = int((valid_dsm_mask & painted).sum())
        report(vol_id, "ORTHO", 98, f"Pass 3: inpainted small holes, coverage now {100.0 * filled_after_inpaint / max(1, valid_dsm_mask.sum().item()):.1f}%.", report_fn)
    elif remaining_holes > 0:
        report(vol_id, "ORTHO", 98, f"Skipping synthetic color inpainting for {remaining_holes} remaining pixels to avoid out-of-context colors.", report_fn)

    final_fill = 100.0 * painted.sum().item() / max(1, valid_dsm_mask.sum().item())
    fallback_success = sum(stats["fallback_painted_pixels"] for stats in image_stats.values())
    black_pixel_analysis = analyze_black_pixels(valid_dsm_mask & (~painted))
    report(
        vol_id,
        "ORTHO",
        98,
        f"Diagnostics: primary painted={sum(stats['primary_painted_pixels'] for stats in image_stats.values())}, fallback painted={fallback_success}, remaining holes={int((valid_dsm_mask & (~painted)).sum())}",
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
                f"image_missing={black_pixel_analysis['fallback_failure_counts']['image_missing']}, "
                f"unknown={black_pixel_analysis['fallback_failure_counts']['unknown']}"
            ),
            report_fn,
        )
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
        "support_distance_histogram": build_diagnostics["support_distance_histogram"],
        "final_fill_percent": float(final_fill),
        "remaining_holes": int((valid_dsm_mask & (~painted)).sum().item()),
        "black_pixel_analysis": black_pixel_analysis,
        "edge_depth_range_m": build_diagnostics["edge_depth_range_m"],
        "edge_min_raw_support": build_diagnostics["edge_min_raw_support"],
        "edge_dilation_px": build_diagnostics["edge_dilation_px"],
        "edge_assignment_dilation_px": build_diagnostics["edge_assignment_dilation_px"],
        "raw_edge_pixel_count": build_diagnostics["raw_edge_pixel_count"],
        "edge_sensitive_pixel_count": build_diagnostics["edge_sensitive_pixel_count"],
        "edge_assignment_pixel_count": build_diagnostics["edge_assignment_pixel_count"],
        "edge_rejected_count": build_diagnostics["edge_rejected_count"],
        "edge_source_depth_tolerance_m": build_diagnostics["edge_source_depth_tolerance_m"],
        "edge_source_depth_neighborhood_px": build_diagnostics["edge_source_depth_neighborhood_px"],
        "edge_source_visibility_checked_count": build_diagnostics["edge_source_visibility_checked_count"],
        "edge_source_visibility_rejected_count": build_diagnostics["edge_source_visibility_rejected_count"],
        "edge_fallback_fill_enabled": enable_edge_fallback_fill,
        "edge_fallback_painted_pixels": int(edge_fallback_painted_pixels),
        "non_edge_fallback_painted_pixels": int(non_edge_fallback_painted_pixels),
        "edge_primary_thresholds": build_diagnostics["edge_primary_thresholds"],
        "edge_fallback_thresholds": build_diagnostics["edge_fallback_thresholds"],
        "primary_image_count": len(valid_images),
        "fallback_image_count": len(fallback_images),
        "thresholds": {
            "primary": build_diagnostics["primary_thresholds"],
            "fallback": build_diagnostics["fallback_thresholds"],
            "primary_margin_px": int(primary_margin_px),
            "fallback_margin_px": int(fallback_margin_px),
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
