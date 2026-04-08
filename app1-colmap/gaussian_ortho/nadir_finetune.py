"""
Nadir fine-tuning for Gaussian models.

Refines SH colour coefficients (and optionally scales/opacity) using
near-nadir training cameras so the model better matches the orthographic
rendering view.  Uses gsplat's rasterisation for forward/backward passes.
"""
import math
import random

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

import gsplat

from .gaussian_model import GaussianModel, num_sh_coefficients
from .scene_info import SceneInfo
from .colmap_loader import CameraInfo


# ---------------------------------------------------------------------------
#  Loss utilities
# ---------------------------------------------------------------------------

def _gaussian_kernel(window_size: int, sigma: float) -> torch.Tensor:
    coords = torch.arange(window_size, dtype=torch.float32) - window_size // 2
    g = torch.exp(-coords ** 2 / (2 * sigma ** 2))
    g /= g.sum()
    return g


def _ssim(img1, img2, window_size=11, C1=0.01 ** 2, C2=0.03 ** 2):
    if img1.dim() == 3:
        img1 = img1.unsqueeze(0)
        img2 = img2.unsqueeze(0)
    channel = img1.shape[1]
    kernel_1d = _gaussian_kernel(window_size, 1.5).to(img1.device)
    kernel_2d = kernel_1d.unsqueeze(1) * kernel_1d.unsqueeze(0)
    window = kernel_2d.expand(channel, 1, window_size, window_size).contiguous()
    mu1 = F.conv2d(img1, window, padding=window_size // 2, groups=channel)
    mu2 = F.conv2d(img2, window, padding=window_size // 2, groups=channel)
    mu1_sq, mu2_sq, mu1_mu2 = mu1 ** 2, mu2 ** 2, mu1 * mu2
    sigma1_sq = F.conv2d(img1 * img1, window, padding=window_size // 2, groups=channel) - mu1_sq
    sigma2_sq = F.conv2d(img2 * img2, window, padding=window_size // 2, groups=channel) - mu2_sq
    sigma12 = F.conv2d(img1 * img2, window, padding=window_size // 2, groups=channel) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / \
               ((mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2))
    return ssim_map.mean()


def dssim_loss(img1, img2, window_size=11):
    return 1.0 - _ssim(img1, img2, window_size)


# ---------------------------------------------------------------------------
#  Image loading
# ---------------------------------------------------------------------------

def _load_image(cam: CameraInfo, data_factor: int, device: torch.device) -> torch.Tensor:
    """Load ground-truth image as (1, H, W, 3) float [0,1], downscaled by data_factor."""
    img = Image.open(cam.image_path).convert("RGB")
    if img.size != (cam.width, cam.height):
        img = img.resize((cam.width, cam.height), Image.LANCZOS)
    if data_factor > 1:
        w, h = img.size
        img = img.resize((w // data_factor, h // data_factor), Image.LANCZOS)
    np_img = np.array(img, dtype=np.float32) / 255.0
    return torch.tensor(np_img, device=device).unsqueeze(0)  # (1, H, W, 3)


# ---------------------------------------------------------------------------
#  Model ↔ gsplat parameter conversion
# ---------------------------------------------------------------------------

def _create_splats(model: GaussianModel, device: torch.device):
    """Convert GaussianModel parameters to gsplat ParameterDict format.

    SH coefficients are stored as a single contiguous 'colors' tensor
    (N, 1+K, 3) instead of separate sh0/shN.
    """
    colors = torch.cat([
        model._features_dc.data.clone(),    # (N, 1, 3)
        model._features_rest.data.clone(),  # (N, K, 3)
    ], dim=1)  # (N, 1+K, 3)
    return torch.nn.ParameterDict({
        "means":     torch.nn.Parameter(model._xyz.data.clone()),
        "scales":    torch.nn.Parameter(model._scaling.data.clone()),
        "quats":     torch.nn.Parameter(model._rotation.data.clone()),
        "opacities": torch.nn.Parameter(model._opacity.data.clone().squeeze(-1)),  # (N,)
        "colors":    torch.nn.Parameter(colors),  # (N, 1+K, 3)
    }).to(device)


def _update_model_from_splats(model: GaussianModel, splats):
    """Copy trained gsplat parameters back into GaussianModel."""
    import torch.nn as nn
    n = splats["means"].shape[0]
    device = splats["means"].device

    model._xyz = nn.Parameter(splats["means"].data)
    model._scaling = nn.Parameter(splats["scales"].data)
    model._rotation = nn.Parameter(splats["quats"].data)
    model._opacity = nn.Parameter(splats["opacities"].data.unsqueeze(-1))
    # Split merged colors back into DC + rest
    colors = splats["colors"].data
    model._features_dc = nn.Parameter(colors[:, :1, :].contiguous())
    model._features_rest = nn.Parameter(colors[:, 1:, :].contiguous())

    # FAGK SH (reset to match new count)
    if model.fagk_enabled:
        n_fagk = num_sh_coefficients(model.fagk_max_degree)
        model._opacity_sh = nn.Parameter(torch.zeros(n, n_fagk, device=device))
    else:
        model._opacity_sh = nn.Parameter(torch.empty(n, 0, device=device))

    # Densification buffers (not needed after training but keep consistent)
    model.xyz_gradient_accum = torch.zeros(n, 1, device=device)
    model.denom = torch.zeros(n, 1, device=device)
    model.max_radii2D = torch.zeros(n, device=device)


# ---------------------------------------------------------------------------
#  Camera data pre-computation
# ---------------------------------------------------------------------------

def _precompute_cameras(cameras, data_factor: int, device: torch.device,
                        norm_center=None, norm_scale=1.0):
    """Precompute viewmats and intrinsics for all training cameras."""
    cam_data = []
    for cam in cameras:
        fx = cam.fx / data_factor if data_factor > 1 else cam.fx
        fy = cam.fy / data_factor if data_factor > 1 else cam.fy
        cx = cam.cx / data_factor if data_factor > 1 else cam.cx
        cy = cam.cy / data_factor if data_factor > 1 else cam.cy
        K = torch.tensor([
            [fx, 0.0, cx],
            [0.0, fy, cy],
            [0.0, 0.0, 1.0],
        ], dtype=torch.float32, device=device)

        cam_pos = cam.T  # camera centre in world coords
        if norm_center is not None:
            cam_pos = (cam_pos - norm_center) / norm_scale

        R_w2c = cam.R.T
        t_w2c = -R_w2c @ cam_pos
        vm = np.eye(4, dtype=np.float32)
        vm[:3, :3] = R_w2c
        vm[:3, 3] = t_w2c
        viewmat = torch.tensor(vm, dtype=torch.float32, device=device)

        cam_data.append({"K": K, "viewmat": viewmat})
    return cam_data


# ---------------------------------------------------------------------------
#  Camera selection
# ---------------------------------------------------------------------------

def _filter_nadir_cameras(cameras, max_angle_deg: float = 15.0):
    """Return cameras whose viewing direction is within *max_angle_deg* of
    the PCA-derived vertical (nadir) direction.
    """
    from numpy.linalg import svd as _svd

    cam_positions = np.array([c.T for c in cameras], dtype=np.float64)
    _, _, Vt = _svd(cam_positions - cam_positions.mean(0), full_matrices=False)
    up = Vt[2]  # smallest eigenvalue direction = vertical

    fwds = np.array([c.R[:, 2] for c in cameras], dtype=np.float64)
    mean_fwd = fwds.mean(axis=0)
    if np.dot(up, mean_fwd) > 0:
        up = -up
    down = -up  # nadir look direction

    dots = np.sum(fwds * down, axis=1)
    dots /= np.maximum(np.linalg.norm(fwds, axis=1), 1e-9)
    angles = np.degrees(np.arccos(np.clip(dots, -1, 1)))

    nadir_cams = [c for c, a in zip(cameras, angles) if a < max_angle_deg]
    return nadir_cams, down


# ---------------------------------------------------------------------------
#  Finetune loop (shared core)
# ---------------------------------------------------------------------------

def _finetune_loop(splats, nadir_cams, cam_data, iterations, sh_degree,
                   optimizers, dc_lr_scale, ssim_lambda, data_factor,
                   device, report_fn=None, report_interval=100):
    """Shared training loop for both SH-only and full nadir fine-tune."""
    bg_color = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float32, device=device)

    for step in range(iterations):
        idx = random.randint(0, len(nadir_cams) - 1)
        cd = cam_data[idx]
        cam = nadir_cams[idx]

        pixels = _load_image(cam, data_factor, device)
        height, width = pixels.shape[1], pixels.shape[2]

        render_colors, render_alphas, info = gsplat.rasterization(
            means=splats["means"],
            quats=splats["quats"],
            scales=torch.exp(splats["scales"]),
            opacities=torch.sigmoid(splats["opacities"]),
            colors=splats["colors"],
            viewmats=cd["viewmat"].unsqueeze(0),
            Ks=cd["K"].unsqueeze(0),
            width=width,
            height=height,
            near_plane=0.01,
            far_plane=1e10,
            sh_degree=sh_degree,
            eps2d=0.3,
            render_mode="RGB",
            rasterize_mode="antialiased",
            packed=True,
        )

        colors = render_colors + bg_color.view(1, 1, 1, 3) * (1.0 - render_alphas)

        l1loss = F.l1_loss(colors, pixels)
        ssimloss = dssim_loss(
            colors.permute(0, 3, 1, 2), pixels.permute(0, 3, 1, 2)
        )
        loss = (1.0 - ssim_lambda) * l1loss + ssim_lambda * ssimloss

        loss.backward()

        with torch.no_grad():
            g = splats["colors"].grad
            if g is not None:
                g[:, :1, :] *= dc_lr_scale

        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)

        if report_fn and step % report_interval == 0:
            report_fn(step, l1loss.item(), len(splats["means"]))

        del pixels, render_colors, render_alphas, colors, info
        del l1loss, ssimloss, loss

        if step % 100 == 0 and device.type == "cuda":
            torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def _prepare_finetune(scene, model, max_angle_deg, data_factor, device):
    """Common setup for both finetune modes. Returns (nadir_cams, splats, cam_data, sh_degree)."""
    nadir_cams, _ = _filter_nadir_cameras(scene.train_cameras, max_angle_deg)
    if len(nadir_cams) < 10:
        print(f"WARNING: only {len(nadir_cams)} nadir cameras (<{max_angle_deg}°),"
              f" falling back to 30° threshold")
        nadir_cams, _ = _filter_nadir_cameras(scene.train_cameras, 30.0)
    if len(nadir_cams) < 5:
        print(f"ERROR: only {len(nadir_cams)} cameras even at 30° — skipping finetune")
        return None

    norm_center = scene.scene_centre.astype(np.float64)
    norm_scale = float(scene.scene_radius)
    if norm_scale < 1e-6:
        norm_scale = 1.0

    splats = _create_splats(model, device)
    with torch.no_grad():
        splats["means"].sub_(torch.tensor(norm_center, dtype=torch.float32, device=device))
        splats["means"].div_(norm_scale)
        splats["scales"].sub_(math.log(norm_scale))

    cam_data = _precompute_cameras(nadir_cams, data_factor, device,
                                   norm_center=norm_center,
                                   norm_scale=norm_scale)
    sh_degree = model.max_sh_degree

    return nadir_cams, splats, cam_data, sh_degree, norm_center, norm_scale


def _finalise_finetune(model, splats, optimizers, cam_data, norm_center, norm_scale, sh_degree, device):
    """Common teardown: denormalise, copy back, clean up."""
    with torch.no_grad():
        splats["means"].mul_(norm_scale).add_(
            torch.tensor(norm_center, dtype=torch.float32, device=device))
        splats["scales"].add_(math.log(norm_scale))

    _update_model_from_splats(model, splats)
    model.active_sh_degree = sh_degree

    del splats, optimizers, cam_data
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    import gc; gc.collect()

    return model


def nadir_finetune(scene: SceneInfo, model: GaussianModel,
                   iterations: int = 2000,
                   data_factor: int = 2,
                   max_angle_deg: float = 15.0,
                   lr_sh0: float = 2.5e-3,
                   lr_shN: float = 1.25e-4,
                   ssim_lambda: float = 0.2,
                   report_fn=None):
    """Fine-tune only SH colour coefficients using near-nadir cameras.

    Geometry (positions, scales, rotations, opacities) is frozen.
    """
    device = model.positions.device

    result = _prepare_finetune(scene, model, max_angle_deg, data_factor, device)
    if result is None:
        return model
    nadir_cams, splats, cam_data, sh_degree, norm_center, norm_scale = result

    print(f"Nadir fine-tune: {len(nadir_cams)} cameras (<{max_angle_deg}°), "
          f"{iterations} iters, data_factor={data_factor}")

    splats["means"].requires_grad_(False)
    splats["scales"].requires_grad_(False)
    splats["quats"].requires_grad_(False)
    splats["opacities"].requires_grad_(False)

    dc_lr_scale = lr_sh0 / max(lr_shN, 1e-10)
    fused = torch.cuda.is_available()
    optimizers = {
        "colors": torch.optim.Adam([splats["colors"]], lr=lr_shN, eps=1e-15, fused=fused),
    }

    _finetune_loop(splats, nadir_cams, cam_data, iterations, sh_degree,
                   optimizers, dc_lr_scale, ssim_lambda, data_factor,
                   device, report_fn)

    print("Nadir fine-tune complete.")
    return _finalise_finetune(model, splats, optimizers, cam_data,
                              norm_center, norm_scale, sh_degree, device)


def nadir_finetune_full(scene: SceneInfo, model: GaussianModel,
                        iterations: int = 3000,
                        data_factor: int = 2,
                        max_angle_deg: float = 15.0,
                        lr_sh0: float = 2.5e-3,
                        lr_shN: float = 1.25e-4,
                        lr_scales: float = 1e-3,
                        lr_opacities: float = 5e-3,
                        ssim_lambda: float = 0.2,
                        report_fn=None):
    """Fine-tune SH coefficients, scales, and opacities using near-nadir cameras.

    Positions and rotations stay frozen (no geometric collapse).
    """
    device = model.positions.device

    result = _prepare_finetune(scene, model, max_angle_deg, data_factor, device)
    if result is None:
        return model
    nadir_cams, splats, cam_data, sh_degree, norm_center, norm_scale = result

    print(f"Nadir fine-tune (full): {len(nadir_cams)} cameras (<{max_angle_deg}°), "
          f"{iterations} iters, data_factor={data_factor}")
    print(f"  Optimising: colors, scales, opacities (positions/rotations frozen)")

    splats["means"].requires_grad_(False)
    splats["quats"].requires_grad_(False)
    splats["scales"].requires_grad_(True)
    splats["opacities"].requires_grad_(True)

    dc_lr_scale = lr_sh0 / max(lr_shN, 1e-10)
    fused = torch.cuda.is_available()
    optimizers = {
        "colors":    torch.optim.Adam([splats["colors"]], lr=lr_shN, eps=1e-15, fused=fused),
        "scales":    torch.optim.Adam([splats["scales"]], lr=lr_scales, eps=1e-15, fused=fused),
        "opacities": torch.optim.Adam([splats["opacities"]], lr=lr_opacities, eps=1e-15, fused=fused),
    }

    _finetune_loop(splats, nadir_cams, cam_data, iterations, sh_degree,
                   optimizers, dc_lr_scale, ssim_lambda, data_factor,
                   device, report_fn)

    print("Nadir fine-tune (full) complete.")
    return _finalise_finetune(model, splats, optimizers, cam_data,
                              norm_center, norm_scale, sh_degree, device)
