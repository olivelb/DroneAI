"""
Orthographic renderer for True Digital Orthophoto Map (TDOM) generation.

Implements the orthogonal splatting from Tortho-Gaussian (Equation 8-10):
  - Orthographic projection matrix P_o
  - Orthographic Jacobian J_o = diag(2/(r-l), 2/(t-b), 0)
  - Virtual camera looking straight down (-Z in geo-aligned coordinates)
  - Rendering at a specified Ground Sample Distance (GSD)
"""
import math

import numpy as np
import torch

from .gaussian_model import GaussianModel
from .rasterizer import (
    RasterSettings, render_ortho,
    make_ortho_proj, make_view_matrix,
    _render_gsplat_inference, _GSPLAT_AVAILABLE,
)
from .colmap_loader import CameraInfo


def compute_ortho_extent(model: GaussianModel, pad: float = 1.0, R_geo: np.ndarray = None):
    """
    Compute the X-Y bounding box of the Gaussian scene.

    If ``R_geo`` is provided (3×3 rotation COLMAP→geo), positions are
    projected into the geo-aligned frame before computing the extent.
    All returned values are in the geo-aligned frame.

    Returns (x_min, x_max, y_min, y_max, z_min, z_max).
    """
    xyz = model.positions.detach()

    if R_geo is not None:
        R_t = torch.tensor(R_geo, dtype=torch.float32, device=xyz.device)
        xyz = (R_t @ xyz.T).T

    lo = torch.quantile(xyz, 0.001, dim=0)
    hi = torch.quantile(xyz, 0.999, dim=0)

    x_lo = lo[0].item() - pad
    x_hi = hi[0].item() + pad
    y_lo = lo[1].item() - pad
    y_hi = hi[1].item() + pad
    z_lo = lo[2].item() - pad
    z_hi = hi[2].item() + pad

    return x_lo, x_hi, y_lo, y_hi, z_lo, z_hi


def _build_ortho_camera(x_min, x_max, y_min, y_max, z_min, z_max):
    """
    Build a virtual top-down camera for orthographic rendering.

    Camera is positioned above the scene centre, looking down -Z.
    """
    cx = (x_min + x_max) / 2.0
    cy = (y_min + y_max) / 2.0
    z_top = z_max + 10.0  # above the scene

    # Camera-to-world: identity rotation (X-right, Y-up, -Z forward)
    # For aerial top-down: camera looks down, so we flip Y and Z
    # World: X=East, Y=North, Z=Up
    # Camera: X=East, Y=-North (screen Y goes down), Z=-Up (looking down)
    R_c2w = np.array([
        [1.0,  0.0,  0.0],
        [0.0, -1.0,  0.0],
        [0.0,  0.0, -1.0],
    ], dtype=np.float32)

    T_world = np.array([cx, cy, z_top], dtype=np.float32)

    return R_c2w, T_world


def render_orthophoto(model: GaussianModel, gsd: float = 0.02,
                      extent: tuple = None, chunk_size: int = 2048,
                      device: torch.device = None, R_geo: np.ndarray = None):
    """
    Render an orthographic TDOM from a trained Gaussian model.

    Parameters
    ----------
    model : GaussianModel
        Trained model (in original COLMAP coordinates — NOT rotated).
    gsd : float
        Ground sample distance in scene units (metres).
    extent : (x_min, x_max, y_min, y_max, z_min, z_max) or None
        Scene bounds in geo-aligned frame.  If None, computed automatically.
    chunk_size : int
        Maximum tile dimension for chunked rendering.
    device : torch.device
        Computation device.
    R_geo : np.ndarray (3, 3) or None
        Rotation from COLMAP coords → geo-aligned (East, North, Up).
        If provided, the ortho camera is oriented to look along the true
        nadir direction **without** rotating the model.  If None, the
        model is assumed to already be geo-aligned (Z = Up).

    Returns
    -------
    dict with:
        'rgb' : np.ndarray (H, W, 3) uint8
        'height' : np.ndarray (H, W) float32  (height map)
        'extent' : (x_min, x_max, y_min, y_max)
        'gsd' : float
    """
    if device is None:
        device = model.positions.device

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

    # If the image is small enough, render in one pass
    if W <= chunk_size and H <= chunk_size:
        rgb, height = _render_single_tile(
            model, x_min, x_max, y_min, y_max, z_min, z_max, W, H, device,
            R_geo=R_geo,
        )
    else:
        # Chunked rendering for large orthophotos
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

                # Tile extent in scene coordinates
                tile_x_min = x_min + px0 * gsd
                tile_x_max = x_min + px1 * gsd
                # Y is flipped (image row 0 = max Y = North)
                tile_y_max = y_max - py0 * gsd
                tile_y_min = y_max - py1 * gsd

                tile_rgb, tile_h = _render_single_tile(
                    model, tile_x_min, tile_x_max, tile_y_min, tile_y_max,
                    z_min, z_max, tw, th, device, R_geo=R_geo,
                )

                rgb[py0:py1, px0:px1] = tile_rgb
                height[py0:py1, px0:px1] = tile_h

                del tile_rgb, tile_h
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

    return {
        "rgb": rgb,
        "height": height,
        "extent": (x_min, x_max, y_min, y_max),
        "gsd": gsd,
    }


def _render_single_tile(model, x_min, x_max, y_min, y_max,
                         z_min, z_max, W, H, device, R_geo=None):
    """Render a single tile via gsplat native orthographic projection.

    All extent parameters (x_min … z_max) are in the **geo-aligned** frame
    (East, North, Up).  The model lives in the original COLMAP frame.

    When ``R_geo`` is provided the camera is oriented to look along the
    true nadir direction in COLMAP coords — **no model rotation needed**.
    Frustum culling is done by projecting positions into the geo frame.
    """
    # --- Per-tile frustum culling (in geo frame) ---
    with torch.no_grad():
        xyz = model.positions  # (N, 3) – COLMAP coords
        if R_geo is not None:
            R_t = torch.tensor(R_geo, dtype=torch.float32, device=device)
            xyz_geo = (R_t @ xyz.T).T
        else:
            xyz_geo = xyz

        max_scale = model.scales.max(dim=-1).values  # (N,)
        margin = max_scale * 3.0
        mask = (
            (xyz_geo[:, 0] + margin >= x_min) & (xyz_geo[:, 0] - margin <= x_max) &
            (xyz_geo[:, 1] + margin >= y_min) & (xyz_geo[:, 1] - margin <= y_max)
        )
        indices = mask.nonzero(as_tuple=False).squeeze(-1)

    if indices.numel() == 0:
        rgb_np = np.full((H, W, 3), 255, dtype=np.uint8)
        height_np = np.zeros((H, W), dtype=np.float32)
        return rgb_np, height_np

    # --- Orthographic camera ---
    # In geo frame: X=East, Y=North, Z=Up.
    # Camera convention: +X=right, +Y=down (screen), +Z=forward (into scene).
    # For nadir: cam-X→East, cam-Y→South(-North), cam-Z→Down(-Up).
    # R_c2w_geo columns: [east, -north, -up] = diag(1, -1, -1).
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
        # Transform camera pose from geo frame back to COLMAP frame.
        R_inv = R_geo.T.astype(np.float64)          # geo → COLMAP
        R_c2w = (R_inv @ R_c2w_geo).astype(np.float32)
        T_world = (R_inv @ np.array([cx_geo, cy_geo, z_cam_geo],
                                     dtype=np.float64)).astype(np.float32)
    else:
        R_c2w = R_c2w_geo.astype(np.float32)
        T_world = np.array([cx_geo, cy_geo, z_cam_geo], dtype=np.float32)

    viewmat = make_view_matrix(R_c2w, T_world)

    # Ortho focal: maps geo-frame extent to image pixels.
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
        viewmatrix=viewmat,
    )

    # Render via gsplat native orthographic projection.
    result = _render_gsplat_inference(model, settings, camera_model="ortho",
                                     indices=indices)

    img = result["image"]  # (3, H, W)
    img_np = (img.clamp(0, 1).detach().cpu().permute(1, 2, 0).numpy() * 255).astype(np.uint8)

    # Height map: convert depth-from-camera to world Z (in geo frame)
    height_np = np.zeros((H, W), dtype=np.float32)
    if "depth" in result:
        depth = result["depth"]  # (1, H, W)
        height_np = z_cam_geo - depth.squeeze(0).detach().cpu().numpy()

    del result
    return img_np, height_np
