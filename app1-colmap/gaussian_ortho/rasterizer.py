"""
Rasterizer wrapper for 3D Gaussian Splatting.

Provides a unified interface for both perspective (training) and
orthographic (TDOM rendering) rasterisation.

Backend priority:
  1. gsplat (pip-installable, supports pinhole + ortho natively)
  2. diff-gaussian-rasterization / diff-gaussian-rasterization-ortho (Tortho_Gaussian)
  3. Pure-PyTorch fallback (low-res, slow, for development only)
"""
import math
from dataclasses import dataclass

import torch
import torch.nn.functional as F
import numpy as np

from .gaussian_model import GaussianModel, SH_C0


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
    bg_color: tuple = (0.0, 0.0, 0.0)
    scaling_modifier: float = 1.0
    viewmatrix: torch.Tensor = None   # 4×4 world-to-camera
    projmatrix: torch.Tensor = None   # 4×4 full projection


# ---------------------------------------------------------------------------
#  Matrix builders
# ---------------------------------------------------------------------------

def make_view_matrix(R_c2w: np.ndarray, T_world: np.ndarray) -> torch.Tensor:
    """Build a 4×4 world-to-camera matrix from camera-to-world R and world-space centre T."""
    R_w2c = R_c2w.T
    t_w2c = -R_w2c @ T_world
    M = np.eye(4, dtype=np.float32)
    M[:3, :3] = R_w2c
    M[:3, 3] = t_w2c
    return torch.tensor(M, dtype=torch.float32)


def make_ortho_proj(left, right, bottom, top, znear, zfar):
    """Build a 4×4 orthographic projection matrix (Equation 9 from Tortho-Gaussian)."""
    P = torch.zeros(4, 4)
    P[0, 0] = 2.0 / (right - left)
    P[1, 1] = 2.0 / (top - bottom)
    P[2, 2] = -2.0 / (zfar - znear)
    P[0, 3] = -(right + left) / (right - left)
    P[1, 3] = -(top + bottom) / (top - bottom)
    P[2, 3] = -(zfar + znear) / (zfar - znear)
    P[3, 3] = 1.0
    return P


# ---------------------------------------------------------------------------
#  Backend detection
# ---------------------------------------------------------------------------

_GSPLAT_AVAILABLE = False
_CUDA_ORTHO_AVAILABLE = False

try:
    import gsplat
    _GSPLAT_AVAILABLE = True
except ImportError:
    pass

try:
    from diff_gaussian_rasterization_ortho import (
        GaussianRasterizationSettings as _CudaOrthoSettings,
        GaussianRasterizer as _CudaOrthoRasterizer,
    )
    _CUDA_ORTHO_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
#  Rendering entry points
# ---------------------------------------------------------------------------

def render_ortho(model: GaussianModel, settings: RasterSettings, indices: torch.Tensor = None):
    """
    Render an orthographic image from the Gaussian model (inference-only).

    Parameters
    ----------
    indices : torch.Tensor (int64), optional
        If provided, only render these Gaussians (for per-tile frustum culling).

    Returns dict with 'image' (3, H, W), 'depth' (1, H, W).
    """
    if _GSPLAT_AVAILABLE:
        return _render_gsplat_inference(model, settings, camera_model="ortho", indices=indices)
    if _CUDA_ORTHO_AVAILABLE:
        device = model.positions.device
        bg = torch.tensor(settings.bg_color, dtype=torch.float32, device=device)
        viewmat = settings.viewmatrix.to(device)
        projmat = settings.projmatrix.to(device)
        return _render_cuda_ortho(model, settings, viewmat, projmat, bg)
    return _render_pytorch_fallback(model, settings, camera_model="ortho")


# ---------------------------------------------------------------------------
#  gsplat inference-only backend (for orthophoto rendering)
# ---------------------------------------------------------------------------

def _render_gsplat_inference(model: GaussianModel, settings: RasterSettings,
                             camera_model: str = "ortho", indices: torch.Tensor = None):
    """
    Lightweight gsplat render for inference — no gradients, no packed meta,
    no absgrad buffers.  Drastically reduces RAM/VRAM for large models.

    When `indices` is provided, only those Gaussians are sent to gsplat,
    drastically reducing VRAM for per-tile ortho rendering.
    """
    device = model.positions.device
    W, H = settings.image_width, settings.image_height

    K = torch.tensor([
        [settings.fx, 0.0, settings.cx],
        [0.0, settings.fy, settings.cy],
        [0.0, 0.0, 1.0],
    ], dtype=torch.float32, device=device).unsqueeze(0)

    viewmat = settings.viewmatrix.to(device).unsqueeze(0)
    bg_rgb = torch.tensor(list(settings.bg_color)[:3], dtype=torch.float32, device=device)

    means = model.positions
    quats = model.rotations
    scales = model.scales * settings.scaling_modifier
    opacities = model.opacity.squeeze(-1)
    colors = model.features

    # Per-tile frustum culling: only send relevant Gaussians to gsplat
    if indices is not None:
        means = means[indices]
        quats = quats[indices]
        scales = scales[indices]
        opacities = opacities[indices]
        colors = colors[indices]

    sh_degree = model.active_sh_degree

    with torch.no_grad():
        # For orthographic rendering, pre-compute SH colours with a uniform
        # nadir view direction.  gsplat normally uses per-Gaussian directions
        # from camera position to each Gaussian, which vary ~17° across the
        # tile when the camera is only 10 units above. Ortho rays are all
        # parallel, so the view direction should be the same for every
        # Gaussian: the camera's forward (+Z) axis in world space.
        raster_sh_degree = sh_degree
        if camera_model == "ortho" and sh_degree is not None and sh_degree > 0:
            c2w = torch.linalg.inv(viewmat[0])  # (4, 4)
            cam_fwd = c2w[:3, 2]  # camera +Z in world = look direction
            dirs = cam_fwd.unsqueeze(0).expand(means.shape[0], 3)  # (N, 3)
            colors = gsplat.spherical_harmonics(sh_degree, dirs, colors)
            colors = torch.clamp_min(colors + 0.5, 0.0)  # match gsplat convention
            raster_sh_degree = None  # pass flat RGB, not SH coefficients

        render_colors, render_alphas, _info = gsplat.rasterization(
            means=means,
            quats=quats,
            scales=scales,
            opacities=opacities,
            colors=colors,
            viewmats=viewmat,
            Ks=K,
            width=W,
            height=H,
            near_plane=settings.znear,
            far_plane=settings.zfar,
            sh_degree=raster_sh_degree,
            eps2d=0.3,
            backgrounds=None,
            render_mode="RGB+ED",
            rasterize_mode="antialiased",
            camera_model=camera_model,
            absgrad=False,
        )

        alpha = render_alphas[0, :, :, 0]
        rgb = render_colors[0, :, :, :3].permute(2, 0, 1)
        rgb = rgb + (1.0 - alpha).unsqueeze(0) * bg_rgb.view(3, 1, 1)
        depth = render_colors[0, :, :, 3:4].permute(2, 0, 1)

    del render_colors, render_alphas, _info

    return {"image": rgb, "depth": depth}


# ---------------------------------------------------------------------------
#  CUDA rasteriser wrapper (Tortho_Gaussian ortho backend)
# ---------------------------------------------------------------------------

def _render_cuda_ortho(model, settings, viewmat, projmat, bg):
    device = model.positions.device
    screenspace_points = torch.zeros_like(model.positions, device=device)

    full_proj = projmat.to(device) @ viewmat.to(device)

    tanfovx = math.tan(0.5 * 2.0 * math.atan(settings.image_width / (2.0 * settings.fx)))
    tanfovy = math.tan(0.5 * 2.0 * math.atan(settings.image_height / (2.0 * settings.fy)))

    raster_settings = _CudaOrthoSettings(
        image_height=settings.image_height,
        image_width=settings.image_width,
        tanfovx=tanfovx,
        tanfovy=tanfovy,
        bg=bg,
        scale_modifier=settings.scaling_modifier,
        viewmatrix=viewmat.T.contiguous(),
        projmatrix=full_proj.T.contiguous(),
        sh_degree=model.active_sh_degree,
        campos=viewmat.inverse()[:3, 3].contiguous(),
        prefiltered=False,
        debug=False,
    )

    rasterizer = _CudaOrthoRasterizer(raster_settings=raster_settings)

    rendered_image, radii = rasterizer(
        means3D=model.positions,
        means2D=screenspace_points,
        shs=model.features,
        colors_precomp=None,
        opacities=model.opacity,
        scales=model.scales,
        rotations=model.rotations,
        cov3D_precomp=None,
    )

    return {
        "image": rendered_image,
        "radii": radii,
        "viewspace_points": screenspace_points,
    }


# ---------------------------------------------------------------------------
#  Pure-PyTorch fallback renderer (simplified alpha-compositing)
# ---------------------------------------------------------------------------

def _render_pytorch_fallback(model, settings, camera_model="pinhole"):
    """
    Differentiable pure-PyTorch Gaussian renderer.

    Renders at a reduced resolution for tractability, then upsamples.
    All operations stay on the computation graph for backprop.
    """
    device = model.positions.device
    ortho = (camera_model == "ortho")
    bg = torch.tensor(settings.bg_color, dtype=torch.float32, device=device)
    viewmat = settings.viewmatrix.to(device)
    projmat = settings.projmatrix.to(device) if settings.projmatrix is not None else None
    W_full, H_full = settings.image_width, settings.image_height

    # Down-scale for speed — max 256 on the long side
    MAX_DIM = 256
    scale_factor = min(1.0, MAX_DIM / max(W_full, H_full))
    W = max(1, int(W_full * scale_factor))
    H = max(1, int(H_full * scale_factor))
    fx = settings.fx * scale_factor
    fy = settings.fy * scale_factor
    cx = settings.cx * scale_factor
    cy = settings.cy * scale_factor

    # Project Gaussian centres to camera space
    xyz = model.positions  # (N, 3)
    ones = torch.ones(xyz.shape[0], 1, device=device)
    xyz_h = torch.cat([xyz, ones], dim=-1)  # (N, 4)

    viewmat_d = viewmat.to(device)
    cam_pts = (viewmat_d @ xyz_h.T).T[:, :3]  # (N, 3)
    depths = cam_pts[:, 2]

    # Filter behind camera
    valid = depths > settings.znear
    if not valid.any():
        img = bg.view(3, 1, 1).expand(3, H_full, W_full)
        return {"image": img, "depth": torch.zeros(1, H_full, W_full, device=device),
                "radii": torch.zeros(xyz.shape[0], device=device),
                "viewspace_points": torch.zeros_like(xyz)}

    # Project to pixel coords
    if ortho:
        projmat_d = projmat.to(device)
        ndc = (projmat_d @ (viewmat_d @ xyz_h.T)).T
        px = (ndc[:, 0] / (ndc[:, 3] + 1e-7) * 0.5 + 0.5) * W
        py = (ndc[:, 1] / (ndc[:, 3] + 1e-7) * 0.5 + 0.5) * H
    else:
        px = fx * cam_pts[:, 0] / (depths + 1e-7) + cx
        py = fy * cam_pts[:, 1] / (depths + 1e-7) + cy

    # Scales → pixel radius (isotropic approx)
    scales = model.scales
    max_scale = scales.max(dim=-1).values
    if ortho:
        sx = 2.0 / (projmat_d[0, 0] + 1e-7) if projmat_d[0, 0] != 0 else W
        radius_px = max_scale * W / sx
    else:
        radius_px = max_scale * fx / (depths + 1e-7)

    # Colours from SH DC
    dc = model._features_dc.squeeze(1)
    colors = (dc * SH_C0 + 0.5).clamp(0, 1)

    opacities = model.opacity.squeeze(-1)  # (N,)

    # Filter visible Gaussians (in-frustum check)
    in_view = valid & (px > -50) & (px < W + 50) & (py > -50) & (py < H + 50)
    if not in_view.any():
        img = bg.view(3, 1, 1).expand(3, H_full, W_full)
        return {"image": img, "depth": torch.zeros(1, H_full, W_full, device=device),
                "radii": torch.zeros(xyz.shape[0], device=device),
                "viewspace_points": torch.zeros_like(xyz)}

    # Sort by depth for front-to-back alpha compositing
    vis_depths = depths[in_view]
    sort_order = vis_depths.argsort()
    vis_px = px[in_view][sort_order]       # (M,)
    vis_py = py[in_view][sort_order]       # (M,)
    vis_col = colors[in_view][sort_order]  # (M, 3)
    vis_opa = opacities[in_view][sort_order]  # (M,)
    vis_rad = radius_px[in_view][sort_order]  # (M,)
    vis_dep = vis_depths[sort_order]        # (M,)
    M = vis_px.shape[0]

    # Build pixel grid (H, W)
    yy = torch.arange(H, device=device, dtype=torch.float32)
    xx = torch.arange(W, device=device, dtype=torch.float32)
    grid_y, grid_x = torch.meshgrid(yy, xx, indexing='ij')  # (H, W)

    # Process in batches to avoid OOM (batch of Gaussians)
    BATCH = min(M, 512)
    image = bg.view(3, 1, 1).expand(3, H, W).clone()
    T = torch.ones(H, W, device=device)  # transmittance
    depth_img = torch.zeros(1, H, W, device=device)

    for b_start in range(0, M, BATCH):
        b_end = min(b_start + BATCH, M)
        b = b_end - b_start

        bx = vis_px[b_start:b_end]        # (b,)
        by = vis_py[b_start:b_end]        # (b,)
        bc = vis_col[b_start:b_end]       # (b, 3)
        ba = vis_opa[b_start:b_end]       # (b,)
        br = vis_rad[b_start:b_end].clamp(min=0.5)  # (b,)
        bd = vis_dep[b_start:b_end]       # (b,)

        # (b, H, W) distance squared
        dx = grid_x.unsqueeze(0) - bx.view(b, 1, 1)  # (b, H, W)
        dy = grid_y.unsqueeze(0) - by.view(b, 1, 1)
        sigma = (br / 3.0).clamp(min=0.3).view(b, 1, 1)
        dist_sq = (dx * dx + dy * dy) / (2.0 * sigma * sigma)

        # Gaussian weight, clamped to avoid computing far-away pixels
        weight = torch.exp(-dist_sq.clamp(max=8.0))  # (b, H, W)
        # Zero out pixels far from Gaussian centre (> 3σ)
        weight = weight * (dist_sq < 9.0).float()

        alpha = ba.view(b, 1, 1) * weight  # (b, H, W)
        alpha = alpha.clamp(max=0.99)

        # Front-to-back compositing per Gaussian in batch
        for i in range(b):
            a_i = alpha[i]  # (H, W)
            contrib = T * a_i
            for c in range(3):
                image[c] = image[c] + contrib * bc[i, c]
            depth_img[0] = depth_img[0] + contrib * bd[i]
            T = T * (1.0 - a_i)

    # Upscale to original resolution
    if scale_factor < 1.0:
        image = F.interpolate(image.unsqueeze(0), size=(H_full, W_full),
                              mode='bilinear', align_corners=False).squeeze(0)
        depth_img = F.interpolate(depth_img.unsqueeze(0), size=(H_full, W_full),
                                  mode='bilinear', align_corners=False).squeeze(0)

    return {
        "image": image.clamp(0, 1),
        "depth": depth_img,
        "radii": radius_px,
        "viewspace_points": torch.stack([px, py, depths], dim=-1),
    }
