"""
Rasteriser wrapper for 3D Gaussian Splatting (CuPy backend).

Uses the custom CUDA rasteriser (cuda_rasterizer.py) for orthographic
rendering.  No PyTorch or gsplat dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

import cupy as cp
import numpy as np

from .gaussian_model import GaussianModel
from .cuda_rasterizer import rasterize_ortho as _cuda_rasterize_ortho


class RasterResult(TypedDict):
    image: cp.ndarray
    depth: cp.ndarray
    alpha: cp.ndarray


# ---------------------------------------------------------------------------
#  Camera settings
# ---------------------------------------------------------------------------

@dataclass
class RasterSettings:
    image_width: int
    image_height: int
    fx: float
    fy: float
    cx: float
    cy: float
    znear: float = 0.01
    zfar: float = 1000.0
    bg_color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scaling_modifier: float = 1.0
    mip_filter_variance: float = 0.03
    mip_filter_compensation: bool = True
    viewmatrix: np.ndarray | None = None  # 4x4 world-to-camera (numpy float32)
    # Optional world-direction transform used only for SH evaluation. A
    # Sim(3) may rotate Gaussian geometry after training, while the learned SH
    # coefficients remain expressed in the original training frame.
    sh_direction_rotation: np.ndarray | None = None


# ---------------------------------------------------------------------------
#  Matrix builders
# ---------------------------------------------------------------------------

def make_view_matrix(R_c2w: np.ndarray, T_world: np.ndarray) -> np.ndarray:
    """Build a 4x4 world-to-camera matrix from camera-to-world R and world T."""
    R_w2c = R_c2w.T
    t_w2c = -R_w2c @ T_world
    matrix: np.ndarray = np.eye(4, dtype=np.float32)
    matrix[:3, :3] = R_w2c
    matrix[:3, 3] = t_w2c
    return matrix


def make_ortho_proj(
    left: float,
    right: float,
    bottom: float,
    top: float,
    znear: float,
    zfar: float,
) -> np.ndarray:
    """Build a 4x4 orthographic projection matrix."""
    projection: np.ndarray = np.zeros((4, 4), dtype=np.float32)
    projection[0, 0] = 2.0 / (right - left)
    projection[1, 1] = 2.0 / (top - bottom)
    projection[2, 2] = -2.0 / (zfar - znear)
    projection[0, 3] = -(right + left) / (right - left)
    projection[1, 3] = -(top + bottom) / (top - bottom)
    projection[2, 3] = -(zfar + znear) / (zfar - znear)
    projection[3, 3] = 1.0
    return projection


# ---------------------------------------------------------------------------
#  Rendering entry point
# ---------------------------------------------------------------------------

def render_ortho(model: GaussianModel, settings: RasterSettings,
                 indices: cp.ndarray | None = None) -> RasterResult:
    """
    Render an orthographic image from the Gaussian model (inference only).

    Parameters
    ----------
    indices : cp.ndarray (int64/int32), optional
        If provided, only render these Gaussians (per-tile frustum culling).

    Returns dict with 'image' (3, H, W) CuPy, 'depth' (1, H, W) CuPy.
    """
    means = model.positions
    quats = model.rotations
    scales = model.scales * settings.scaling_modifier
    opacities = model.opacity.squeeze(-1)
    sh_coeffs = model.features
    sh_degree = model.active_sh_degree

    if indices is not None:
        means = means[indices]
        quats = quats[indices]
        scales = scales[indices]
        opacities = opacities[indices]
        sh_coeffs = sh_coeffs[indices]

    if settings.viewmatrix is None:
        raise ValueError("RasterSettings.viewmatrix is required")
    viewmat = cp.array(settings.viewmatrix, dtype=cp.float32)

    rgb, depth, alpha = _cuda_rasterize_ortho(
        means, quats, scales, opacities, sh_coeffs, sh_degree,
        viewmat,
        settings.fx, settings.fy, settings.cx, settings.cy,
        settings.image_width, settings.image_height,
        settings.znear, settings.zfar,
        settings.bg_color,
        eps2d=settings.mip_filter_variance,
        compensate_filter=settings.mip_filter_compensation,
        sh_direction_rotation=settings.sh_direction_rotation,
    )

    # Return in (C, H, W) layout for compatibility with callers
    image: cp.ndarray = rgb.transpose(2, 0, 1)  # (H, W, 3) -> (3, H, W)
    depth_out: cp.ndarray = depth[None, :, :]  # (H, W) -> (1, H, W)
    alpha_out: cp.ndarray = alpha[None, :, :]  # (H, W) -> (1, H, W)

    return {"image": image, "depth": depth_out, "alpha": alpha_out}
