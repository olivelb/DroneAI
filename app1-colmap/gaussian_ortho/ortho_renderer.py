"""
Orthographic renderer for True Digital Orthophoto Map (TDOM) generation.

CuPy backend — no PyTorch dependency.

Implements the orthogonal splatting from Tortho-Gaussian (Eq. 8-10):
  - Orthographic projection matrix P_o
  - Orthographic Jacobian J_o = diag(2/(r-l), 2/(t-b), 0)
  - Virtual camera looking straight down (-Z in geo-aligned coordinates)
  - Rendering at a specified Ground Sample Distance (GSD)
"""
import math

import cupy as cp
import numpy as np

from .gaussian_model import GaussianModel
from .rasterizer import RasterSettings, render_ortho, make_view_matrix


def compute_ortho_extent(model: GaussianModel, pad: float = 1.0,
                         R_geo: np.ndarray = None):
    """
    Compute the X-Y bounding box of the Gaussian scene.

    Returns (x_min, x_max, y_min, y_max, z_min, z_max) in geo-aligned frame.
    """
    xyz = model.positions      # CuPy array

    if R_geo is not None:
        R_t = cp.array(R_geo, dtype=cp.float32)
        xyz = (R_t @ xyz.T).T

    lo = cp.quantile(xyz, 0.001, axis=0)
    hi = cp.quantile(xyz, 0.999, axis=0)

    x_lo = float(lo[0]) - pad
    x_hi = float(hi[0]) + pad
    y_lo = float(lo[1]) - pad
    y_hi = float(hi[1]) + pad
    z_lo = float(lo[2]) - pad
    z_hi = float(hi[2]) + pad

    return x_lo, x_hi, y_lo, y_hi, z_lo, z_hi


def render_orthophoto(model: GaussianModel, gsd: float = 0.02,
                      extent: tuple = None, chunk_size: int = 0,
                      device=None, R_geo: np.ndarray = None,
                      sh_direction_rotation: np.ndarray = None,
                      mip_filter_variance: float = 0.03,
                      mip_filter_compensation: bool = True):
    """
    Render an orthographic TDOM from a trained Gaussian model.

    Parameters
    ----------
    model : GaussianModel
        Trained model (COLMAP coordinates).
    gsd : float
        Ground sample distance in scene units (metres).
    extent : (x_min, x_max, y_min, y_max, z_min, z_max) or None
    chunk_size : int
        Max tile dimension for chunked rendering.  0 = auto-select.
    device : ignored (kept for API compatibility).
    R_geo : np.ndarray (3, 3) or None
        Rotation COLMAP → geo-aligned (East, North, Up).

    Returns
    -------
    dict with 'rgb', 'height', 'extent', 'gsd'
    """
    if extent is None:
        x_min, x_max, y_min, y_max, z_min, z_max = compute_ortho_extent(
            model, R_geo=R_geo)
    else:
        x_min, x_max, y_min, y_max, z_min, z_max = extent

    W = int(math.ceil((x_max - x_min) / gsd))
    H = int(math.ceil((y_max - y_min) / gsd))

    if W <= 0 or H <= 0:
        raise ValueError(f"Invalid ortho dimensions: {W}x{H} from extent "
                         f"({x_min:.2f},{x_max:.2f}), ({y_min:.2f},{y_max:.2f}), gsd={gsd}")

    # Auto-select chunk_size based on available GPU VRAM.
    if chunk_size <= 0:
        chunk_size = 2048
        try:
            free_bytes, total_bytes = cp.cuda.Device(0).mem_info
            free_mb = free_bytes / (1024 ** 2)
            available = max(free_mb - 200, 100)
            max_pixels = int(available * 1024 ** 2 / 20)
            side = int(math.sqrt(max_pixels))
            chunk_size = min(4096, max(512, side))
            chunk_size = (chunk_size // 256) * 256
        except Exception:
            chunk_size = 1024

    # Single tile or chunked
    if W <= chunk_size and H <= chunk_size:
        rgb, height = _render_single_tile(
            model, x_min, x_max, y_min, y_max, z_min, z_max, W, H,
            R_geo=R_geo, sh_direction_rotation=sh_direction_rotation,
            mip_filter_variance=mip_filter_variance,
            mip_filter_compensation=mip_filter_compensation,
        )
    else:
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        height = np.zeros((H, W), dtype=np.float32)

        n_tiles_x = math.ceil(W / chunk_size)
        n_tiles_y = math.ceil(H / chunk_size)

        for ty in range(n_tiles_y):
            for tx in range(n_tiles_x):
                px0 = tx * chunk_size
                py0 = ty * chunk_size
                px1 = min(px0 + chunk_size, W)
                py1 = min(py0 + chunk_size, H)
                tw, th = px1 - px0, py1 - py0

                tile_x_min = x_min + px0 * gsd
                tile_x_max = x_min + px1 * gsd
                tile_y_max = y_max - py0 * gsd
                tile_y_min = y_max - py1 * gsd

                tile_rgb, tile_h = _render_single_tile(
                    model, tile_x_min, tile_x_max, tile_y_min, tile_y_max,
                    z_min, z_max, tw, th, R_geo=R_geo,
                    sh_direction_rotation=sh_direction_rotation,
                    mip_filter_variance=mip_filter_variance,
                    mip_filter_compensation=mip_filter_compensation,
                )

                rgb[py0:py1, px0:px1] = tile_rgb
                height[py0:py1, px0:px1] = tile_h

                del tile_rgb, tile_h
                cp.get_default_memory_pool().free_all_blocks()

    return {
        "rgb": rgb,
        "height": height,
        "extent": (x_min, x_max, y_min, y_max),
        "gsd": gsd,
    }


def _render_single_tile(model, x_min, x_max, y_min, y_max,
                         z_min, z_max, W, H, R_geo=None,
                         sh_direction_rotation=None,
                         mip_filter_variance=0.03,
                         mip_filter_compensation=True):
    """Render one tile via the custom CUDA ortho rasteriser."""
    # --- Per-tile frustum culling (in geo frame) ---
    xyz = model.positions      # (N, 3) COLMAP coords
    if R_geo is not None:
        R_t = cp.array(R_geo, dtype=cp.float32)
        xyz_geo = (R_t @ xyz.T).T
    else:
        xyz_geo = xyz

    max_scale = model.scales.max(axis=-1)
    margin = max_scale * 3.0
    mask = (
        (xyz_geo[:, 0] + margin >= x_min) & (xyz_geo[:, 0] - margin <= x_max) &
        (xyz_geo[:, 1] + margin >= y_min) & (xyz_geo[:, 1] - margin <= y_max)
    )
    indices = cp.nonzero(mask)[0]

    if indices.size == 0:
        rgb_np = np.full((H, W, 3), 255, dtype=np.uint8)
        height_np = np.zeros((H, W), dtype=np.float32)
        return rgb_np, height_np

    # --- Orthographic camera ---
    R_c2w_geo = np.array([
        [1.0,  0.0,  0.0],
        [0.0, -1.0,  0.0],
        [0.0,  0.0, -1.0],
    ], dtype=np.float64)

    cx_geo = (x_min + x_max) / 2.0
    cy_geo = (y_min + y_max) / 2.0
    scene_height = max(z_max - z_min, 0.1)
    z_cam_geo = z_max + 10.0

    if R_geo is not None:
        R_inv = R_geo.T.astype(np.float64)
        R_c2w = (R_inv @ R_c2w_geo).astype(np.float32)
        T_world = (R_inv @ np.array([cx_geo, cy_geo, z_cam_geo],
                                     dtype=np.float64)).astype(np.float32)
    else:
        R_c2w = R_c2w_geo.astype(np.float32)
        T_world = np.array([cx_geo, cy_geo, z_cam_geo], dtype=np.float32)

    viewmat = make_view_matrix(R_c2w, T_world)

    tile_w = x_max - x_min
    tile_h = y_max - y_min
    fx = W / tile_w if tile_w > 0 else 1.0
    fy = H / tile_h if tile_h > 0 else 1.0

    znear = 0.01
    zfar = scene_height + 20.0

    settings = RasterSettings(
        image_width=W, image_height=H,
        fx=fx, fy=fy, cx=W / 2.0, cy=H / 2.0,
        znear=znear, zfar=zfar,
        bg_color=(1.0, 1.0, 1.0),
        mip_filter_variance=mip_filter_variance,
        mip_filter_compensation=mip_filter_compensation,
        viewmatrix=viewmat,
        sh_direction_rotation=sh_direction_rotation,
    )

    result = render_ortho(model, settings, indices=indices)

    img = result["image"]
    img_np = (cp.clip(img, 0, 1).transpose(1, 2, 0).get() * 255).astype(np.uint8)

    height_np = np.zeros((H, W), dtype=np.float32)
    if "depth" in result:
        from .height_reference import depth_buffer_to_height

        depth = result["depth"]
        height_np = depth_buffer_to_height(depth.squeeze(0).get(), z_cam_geo)

    del result
    return img_np, height_np
