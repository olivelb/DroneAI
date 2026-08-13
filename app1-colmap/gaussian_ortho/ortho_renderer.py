"""
Orthographic renderer for True Digital Orthophoto Map (TDOM) generation.

CuPy backend — no PyTorch dependency.

Implements the orthogonal splatting from Tortho-Gaussian (Eq. 8-10):
  - Orthographic projection matrix P_o
  - Orthographic Jacobian J_o = diag(2/(r-l), 2/(t-b), 0)
  - Virtual camera looking straight down (-Z in geo-aligned coordinates)
  - Rendering at a specified Ground Sample Distance (GSD)
"""
from __future__ import annotations

import math
from typing import TypedDict

import cupy as cp
import numpy as np

from .gaussian_model import GaussianModel
from .rasterizer import RasterSettings, render_ortho, make_view_matrix


type OrthoExtent = tuple[float, float, float, float, float, float]


class OrthophotoResult(TypedDict):
    rgb: np.ndarray
    height: np.ndarray
    extent: tuple[float, float, float, float]
    gsd: float


def _prepare_culling_geometry(
    model: GaussianModel,
    *,
    R_geo: np.ndarray | None,
    frame_origin: np.ndarray | None,
) -> tuple[cp.ndarray, cp.ndarray]:
    """Transform invariant culling inputs once for every output tile."""

    positions = model.positions
    if frame_origin is not None:
        positions = positions - cp.asarray(
            frame_origin,
            dtype=cp.float32,
        )[None, :]
    if R_geo is not None:
        rotation = cp.asarray(R_geo, dtype=cp.float32)
        positions = (rotation @ positions.T).T
    return positions, model.scales.max(axis=-1) * 3.0


def compute_ortho_extent(model: GaussianModel, pad: float = 1.0,
                         R_geo: np.ndarray | None = None,
                         frame_origin: np.ndarray | None = None,
                         quantile: float = 0.001) -> OrthoExtent:
    """
    Compute the X-Y bounding box of the Gaussian scene.

    Returns (x_min, x_max, y_min, y_max, z_min, z_max) in geo-aligned frame.
    """
    xyz = model.positions      # CuPy array

    if frame_origin is not None:
        xyz = xyz - cp.array(frame_origin, dtype=cp.float32)[None, :]
    if R_geo is not None:
        R_t = cp.array(R_geo, dtype=cp.float32)
        xyz = (R_t @ xyz.T).T

    if not 0.0 <= quantile < 0.5:
        raise ValueError("extent quantile must be in [0, 0.5)")
    lo = cp.quantile(xyz, quantile, axis=0)
    hi = cp.quantile(xyz, 1.0 - quantile, axis=0)

    x_lo = float(lo[0]) - pad
    x_hi = float(hi[0]) + pad
    y_lo = float(lo[1]) - pad
    y_hi = float(hi[1]) + pad
    z_lo = float(lo[2]) - pad
    z_hi = float(hi[2]) + pad

    return x_lo, x_hi, y_lo, y_hi, z_lo, z_hi


def render_orthophoto(model: GaussianModel, gsd: float = 0.02,
                      extent: OrthoExtent | None = None, chunk_size: int = 0,
                      device: object | None = None,
                      R_geo: np.ndarray | None = None,
                      frame_origin: np.ndarray | None = None,
                      sh_direction_rotation: np.ndarray | None = None,
                      mip_filter_variance: float = 0.03,
                      mip_filter_compensation: bool = True) -> OrthophotoResult:
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
            model, R_geo=R_geo, frame_origin=frame_origin)
    else:
        x_min, x_max, y_min, y_max, z_min, z_max = extent

    W = math.ceil((x_max - x_min) / gsd)
    H = math.ceil((y_max - y_min) / gsd)

    if W <= 0 or H <= 0:
        raise ValueError(f"Invalid ortho dimensions: {W}x{H} from extent "
                         f"({x_min:.2f},{x_max:.2f}), ({y_min:.2f},{y_max:.2f}), gsd={gsd}")

    # The culling geometry is invariant across output tiles.  Computing the
    # frame transform and activated maximum scale once avoids repeatedly
    # exponentiating and rotating every resident Gaussian.
    culling_positions, culling_margin = _prepare_culling_geometry(
        model,
        R_geo=R_geo,
        frame_origin=frame_origin,
    )

    # Auto-select chunk_size based on available GPU VRAM.
    if chunk_size <= 0:
        chunk_size = 2048
        try:
            free_bytes, _total_bytes = cp.cuda.Device(0).mem_info
            free_mb = free_bytes / (1024 ** 2)
            available = max(free_mb - 200, 100)
            max_pixels = int(available * 1024 ** 2 / 20)
            side = int(math.sqrt(max_pixels))
            chunk_size = min(4096, max(512, side))
            chunk_size = (chunk_size // 256) * 256
        except Exception:
            chunk_size = 1024

    # Single tile or chunked
    if chunk_size >= max(W, H):
        rgb, height = _render_single_tile(
            model, x_min, x_max, y_min, y_max, z_min, z_max, W, H,
            R_geo=R_geo, frame_origin=frame_origin,
            sh_direction_rotation=sh_direction_rotation,
            mip_filter_variance=mip_filter_variance,
            mip_filter_compensation=mip_filter_compensation,
            culling_positions=culling_positions,
            culling_margin=culling_margin,
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
                    frame_origin=frame_origin,
                    sh_direction_rotation=sh_direction_rotation,
                    mip_filter_variance=mip_filter_variance,
                    mip_filter_compensation=mip_filter_compensation,
                    culling_positions=culling_positions,
                    culling_margin=culling_margin,
                )

                rgb[py0:py1, px0:px1] = tile_rgb
                height[py0:py1, px0:px1] = tile_h

                del tile_rgb, tile_h

    return {
        "rgb": rgb,
        "height": height,
        "extent": (x_min, x_max, y_min, y_max),
        "gsd": gsd,
    }


def _render_single_tile(
    model: GaussianModel,
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
    z_min: float,
    z_max: float,
    width: int,
    height: int,
    R_geo: np.ndarray | None = None,
    frame_origin: np.ndarray | None = None,
    sh_direction_rotation: np.ndarray | None = None,
    mip_filter_variance: float = 0.03,
    mip_filter_compensation: bool = True,
    culling_positions: cp.ndarray | None = None,
    culling_margin: cp.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Render one tile via the custom CUDA ortho rasteriser."""
    from .height_reference import depth_buffer_to_height, empty_height_map

    # --- Per-tile frustum culling (in geo frame) ---
    if culling_positions is None or culling_margin is None:
        culling_positions, culling_margin = _prepare_culling_geometry(
            model,
            R_geo=R_geo,
            frame_origin=frame_origin,
        )
    mask = (
        (culling_positions[:, 0] + culling_margin >= x_min)
        & (culling_positions[:, 0] - culling_margin <= x_max)
        & (culling_positions[:, 1] + culling_margin >= y_min)
        & (culling_positions[:, 1] - culling_margin <= y_max)
    )
    indices = cp.nonzero(mask)[0]

    if indices.size == 0:
        rgb_np: np.ndarray = np.full((height, width, 3), 255, dtype=np.uint8)
        height_np: np.ndarray = empty_height_map(height, width)
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
        R_inv: np.ndarray = R_geo.T.astype(np.float64)
        R_c2w = (R_inv @ R_c2w_geo).astype(np.float32)
        T_world = R_inv @ np.array(
            [cx_geo, cy_geo, z_cam_geo], dtype=np.float64
        )
        if frame_origin is not None:
            T_world += np.asarray(frame_origin, dtype=np.float64)
        T_world = T_world.astype(np.float32)
    else:
        R_c2w = R_c2w_geo.astype(np.float32)
        T_world = np.array([cx_geo, cy_geo, z_cam_geo], dtype=np.float32)

    viewmat = make_view_matrix(R_c2w, T_world)

    tile_w = x_max - x_min
    tile_h = y_max - y_min
    fx = width / tile_w if tile_w > 0 else 1.0
    fy = height / tile_h if tile_h > 0 else 1.0

    znear = 0.01
    zfar = scene_height + 20.0

    settings = RasterSettings(
        image_width=width, image_height=height,
        fx=fx, fy=fy, cx=width / 2.0, cy=height / 2.0,
        znear=znear, zfar=zfar,
        # DroneGS learns against black. Render premultiplied radiance on that
        # background, then recover the surface colour below; compositing
        # translucent splats directly on white washes out the map.
        bg_color=(0.0, 0.0, 0.0),
        mip_filter_variance=mip_filter_variance,
        mip_filter_compensation=mip_filter_compensation,
        viewmatrix=viewmat,
        sh_direction_rotation=sh_direction_rotation,
    )

    result = render_ortho(model, settings, indices=indices)

    img = result["image"]
    alpha = result["alpha"]
    valid = alpha > 1.0e-6
    surface_rgb = cp.where(valid, img / cp.maximum(alpha, 1.0e-6), 1.0)
    img_np: np.ndarray = (
        cp.clip(surface_rgb, 0, 1).transpose(1, 2, 0).get() * 255
    ).astype(np.uint8)

    depth = result["depth"]
    height_np = depth_buffer_to_height(depth.squeeze(0).get(), z_cam_geo)

    del result
    return img_np, height_np
