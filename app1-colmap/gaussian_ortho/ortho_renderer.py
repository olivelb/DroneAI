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
)
from .colmap_loader import CameraInfo


def compute_ortho_extent(model: GaussianModel, pad: float = 1.0):
    """
    Compute the X-Y bounding box of the Gaussian scene.

    Uses position-based bounds with a small percentile clip to exclude
    outlier Gaussians that would blow up the extent.

    Returns (x_min, x_max, y_min, y_max, z_min, z_max).
    """
    xyz = model.positions.detach()

    # Use 0.1th / 99.9th percentile of positions to reject outliers,
    # then add a generous fixed pad (metres).
    lo = torch.quantile(xyz, 0.001, dim=0)  # (3,)
    hi = torch.quantile(xyz, 0.999, dim=0)  # (3,)

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
                      extent: tuple = None, chunk_size: int = 4096,
                      device: torch.device = None):
    """
    Render an orthographic TDOM from a trained Gaussian model.

    Parameters
    ----------
    model : GaussianModel
        Trained model.
    gsd : float
        Ground sample distance in scene units (metres).
    extent : (x_min, x_max, y_min, y_max, z_min, z_max) or None
        Scene bounds. If None, computed from Gaussian positions.
    chunk_size : int
        Maximum tile dimension for chunked rendering.
    device : torch.device
        Computation device.

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
        x_min, x_max, y_min, y_max, z_min, z_max = compute_ortho_extent(model)
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
            model, x_min, x_max, y_min, y_max, z_min, z_max, W, H, device
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
                    z_min, z_max, tw, th, device
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
                         z_min, z_max, W, H, device):
    """Render a single tile via orthographic splatting with per-tile frustum culling."""
    # --- Per-tile frustum culling (XY) ---
    # Only send Gaussians whose XY position (± max scale margin) overlaps this tile.
    # This prevents OOM when the full model has millions of Gaussians.
    with torch.no_grad():
        xyz = model.positions  # (N, 3)
        # Margin: max scale of each Gaussian × 3 (covers 3σ splat footprint)
        max_scale = model.scales.max(dim=-1).values  # (N,)
        margin = max_scale * 3.0
        mask = (
            (xyz[:, 0] + margin >= x_min) & (xyz[:, 0] - margin <= x_max) &
            (xyz[:, 1] + margin >= y_min) & (xyz[:, 1] - margin <= y_max)
        )
        indices = mask.nonzero(as_tuple=False).squeeze(-1)

    if indices.numel() == 0:
        # No Gaussians in this tile — return white background
        rgb_np = np.full((H, W, 3), 255, dtype=np.uint8)
        height_np = np.zeros((H, W), dtype=np.float32)
        return rgb_np, height_np

    R_c2w, T_world = _build_ortho_camera(x_min, x_max, y_min, y_max, z_min, z_max)

    viewmat = make_view_matrix(R_c2w, T_world)

    # Orthographic projection: maps scene extent to [-1, 1] NDC
    # left=x_min, right=x_max, bottom=y_min, top=y_max (in camera space)
    # Since camera is flipped, we need to map correctly
    half_w = (x_max - x_min) / 2.0
    half_h = (y_max - y_min) / 2.0
    znear = 0.01
    zfar = (z_max - z_min) + 20.0

    projmat = make_ortho_proj(
        left=-half_w, right=half_w,
        bottom=-half_h, top=half_h,
        znear=znear, zfar=zfar,
    )

    # For the CUDA ortho rasteriser, fx/fy encode the NDC mapping
    fx = W / (2.0 * half_w) if half_w > 0 else 1.0
    fy = H / (2.0 * half_h) if half_h > 0 else 1.0

    settings = RasterSettings(
        image_width=W, image_height=H,
        fx=fx, fy=fy, cx=W / 2.0, cy=H / 2.0,
        znear=znear, zfar=zfar,
        bg_color=(1.0, 1.0, 1.0),
        viewmatrix=viewmat,
        projmatrix=projmat,
    )

    result = render_ortho(model, settings, indices=indices)

    img = result["image"]  # (3, H, W)
    img_np = (img.clamp(0, 1).detach().cpu().permute(1, 2, 0).numpy() * 255).astype(np.uint8)

    # Height map from depth
    height_np = np.zeros((H, W), dtype=np.float32)
    if "depth" in result:
        depth = result["depth"]  # (1, H, W)
        # Convert depth-in-camera to world Z
        z_top = z_max + 10.0
        height_np = z_top - depth.squeeze(0).detach().cpu().numpy()

    # Free GPU tensors from rasterisation
    del result

    return img_np, height_np
