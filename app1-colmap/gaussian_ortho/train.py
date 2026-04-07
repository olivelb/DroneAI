"""
Fast training loop for 3D Gaussian Splatting using gsplat's DefaultStrategy.

Uses gsplat's built-in densification strategy (clone/split/prune) instead
of manual gradient accumulation.  Key improvements over the previous version:
  - gsplat DefaultStrategy handles all adaptive density control correctly
  - Per-parameter optimisers (gsplat convention)
  - ExponentialLR decay for means
  - Image downscaling via data_factor for speed / VRAM
  - Progressive SH degree activation
"""
import math
import os
import random
from dataclasses import dataclass, field

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

# Set CUDA allocator to use expandable segments (reduces fragmentation OOMs)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import gsplat
from gsplat.strategy import DefaultStrategy, MCMCStrategy

from .gaussian_model import GaussianModel
from .scene_info import SceneInfo
from .colmap_loader import CameraInfo


# ---------------------------------------------------------------------------
#  Training hyperparameters
# ---------------------------------------------------------------------------

@dataclass
class TrainConfig:
    iterations: int = 30_000
    # Image downscaling (4 = quarter-res, fast; 1 = full-res, best quality)
    data_factor: int = 4
    # Progressive resolution: train at coarser scale early, ramp to data_factor.
    # Schedule is a list of (fraction_of_training, data_factor) breakpoints.
    # E.g. [(0.0, 8), (0.3, 4), (0.6, 2), (0.8, 1)] means:
    #   0-30%: df=8, 30-60%: df=4, 60-80%: df=2, 80-100%: df=1
    # Set to None to disable (use data_factor throughout).
    progressive_schedule: list = None
    # Loss weights (same as gsplat default: 0.8 L1 + 0.2 SSIM)
    ssim_lambda: float = 0.2
    # Learning rates (gsplat defaults)
    lr_means: float = 1.6e-4
    lr_scales: float = 5e-3
    lr_opacities: float = 5e-2
    lr_quats: float = 1e-3
    lr_sh0: float = 2.5e-3
    lr_shN: float = 1.25e-4   # 2.5e-3 / 20
    # SH degree
    sh_degree: int = 3
    sh_degree_interval: int = 1000
    # Strategy: "mcmc" (bounded, recommended) or "default" (unbounded growth)
    strategy: str = "mcmc"
    # DefaultStrategy densification
    refine_start_iter: int = 500
    refine_stop_iter: int = 25_000
    refine_every: int = 100
    reset_every: int = 3000
    # MCMCStrategy
    cap_max: int = 1_000_000
    noise_lr: float = 5e5
    # Regularisation (used with MCMC)
    opacity_reg: float = 0.01
    scale_reg: float = 0.01
    # Maximum normalised scale per axis.  Prevents Gaussians from growing
    # so large that the tile-intersection list (isect_ids) blows up VRAM.
    # 0.1 normalised ≈ 50 px at df=4 → ~31 tiles per GS.
    # 0.2 normalised ≈ 100 px at df=4 → ~123 tiles per GS.
    # Set to 0 to disable the cap.
    max_scale: float = 0.1
    # Initial opacity (0.1 for default, 0.5 for mcmc)
    init_opa: float = 0.1
    # Initial scale multiplier (1.0 for default, 0.1 for mcmc per gsplat official example).
    # Smaller initial Gaussians help MCMC convergence: they cover less area so
    # the photometric loss signal is more localised, and MCMC relocation/growth
    # handles filling gaps.
    init_scale: float = 0.1
    # Random background during training (helps avoid transparency / floaters).
    # Strongly recommended for MCMC. Modulated backgrounds prevent semi-
    # transparent Gaussians from converging to a constant background colour.
    random_bkgd: bool = True
    # Fraction of training to use random backgrounds. After this, switch to
    # white so the model settles with the actual render-time background.
    random_bkgd_end_frac: float = 0.75
    # Checkpoints
    checkpoint_iterations: list = field(default_factory=lambda: [7_000, 30_000])
    # Output
    output_dir: str = "output/gaussian"
    # Random seed
    seed: int = 42
    # Per-image appearance compensation (removes flight-strip banding)
    appearance_enabled: bool = True
    appearance_dim: int = 16
    appearance_lr: float = 1e-3
    # Spatial pruning during training: mark far-away Gaussians as dead so
    # MCMC's relocate recycling them to useful positions near the cameras.
    spatial_prune_every: int = 500   # 0 to disable
    # Ortho coverage regularisation (removes nadir banding artefacts)
    ortho_reg: float = 0.0       # weight (0 to disable) — currently broken with scene normalisation
    ortho_reg_start: int = 500   # iteration to start
    ortho_reg_every: int = 20    # apply every N iterations
    ortho_crop_px: int = 256     # ortho crop size in pixels


# ---------------------------------------------------------------------------
#  D-SSIM loss (PyTorch fallback)
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
#  Per-image appearance model (affine colour transform)
# ---------------------------------------------------------------------------

class AppearanceModel(torch.nn.Module):
    """Per-image affine colour correction to decouple transient exposure
    variations from persistent Gaussian colours.

    Each training image gets a learnable embedding that produces per-channel
    scale and bias.  Applied to the **rendered image** so that per-image
    lighting variations are absorbed by the appearance model, while the
    Gaussians learn canonical scene colours.  At ortho-render time this
    module is NOT used, and the raw Gaussian colours are already correct.
    """

    def __init__(self, n_images: int, embed_dim: int = 16):
        super().__init__()
        self.embeds = torch.nn.Embedding(n_images, embed_dim)
        torch.nn.init.zeros_(self.embeds.weight)
        # Small MLP: embed → 6 (scale_r, scale_g, scale_b, bias_r, bias_g, bias_b)
        self.mlp = torch.nn.Sequential(
            torch.nn.Linear(embed_dim, embed_dim),
            torch.nn.ReLU(inplace=True),
            torch.nn.Linear(embed_dim, 6),
        )
        # Init output layer to identity transform (scale=1, bias=0)
        torch.nn.init.zeros_(self.mlp[-1].weight)
        torch.nn.init.zeros_(self.mlp[-1].bias)

    def forward(self, img_idx: int, rendered: torch.Tensor) -> torch.Tensor:
        """Apply per-image colour correction to rendered image.

        Parameters
        ----------
        img_idx : int
            Index of the training image.
        rendered : (1, H, W, 3) float tensor
            Rendered RGB before loss computation.

        Returns
        -------
        (1, H, W, 3) corrected colours.
        """
        embed = self.embeds(torch.tensor(img_idx, device=rendered.device))
        params = self.mlp(embed)  # (6,)
        # Scale constrained to [0.8, 1.2]: allows ±20% exposure correction
        # but cannot collapse to zero (which killed color diversity).
        scale = 0.8 + torch.sigmoid(params[:3]) * 0.4
        bias = params[3:] * 0.05  # small bias range (±5%)
        return rendered * scale.view(1, 1, 1, 3) + bias.view(1, 1, 1, 3)


# ---------------------------------------------------------------------------
#  Ortho coverage regularisation
# ---------------------------------------------------------------------------

def _estimate_nadir_frame(cameras):
    """Estimate the nadir-looking frame from training camera orientations.

    Returns R_c2w (3×3 float32): rotation matrix for a virtual top-down camera.
    Camera axes: X≈right, Y≈down-in-image, -Z=viewing direction (down).
    """
    # Average camera forward direction (column 2 of R = optical axis in world)
    forwards = np.stack([cam.R[:, 2] for cam in cameras], axis=0)
    mean_fwd = forwards.mean(axis=0)
    mean_fwd /= np.linalg.norm(mean_fwd) + 1e-8

    # Average right direction (column 0)
    rights = np.stack([cam.R[:, 0] for cam in cameras], axis=0)
    mean_right = rights.mean(axis=0)
    # Orthogonalise right w.r.t. forward
    mean_right -= np.dot(mean_right, mean_fwd) * mean_fwd
    mean_right /= np.linalg.norm(mean_right) + 1e-8

    # Camera Y = forward × right (points "down" in the image)
    mean_down = np.cross(mean_fwd, mean_right)
    mean_down /= np.linalg.norm(mean_down) + 1e-8

    # R_c2w columns: [right, down, forward]
    # forward ≈ scene-down for nadir cameras, -Z_cam = forward → looks down
    R_c2w = np.column_stack([mean_right, mean_down, mean_fwd]).astype(np.float32)
    return R_c2w


def _ortho_coverage_loss(splats, R_c2w_ortho, scene_lo, scene_hi,
                         crop_px, device, cameras=None):
    """Render a random small ortho crop and return coverage + uniformity loss.

    The loss is fully differentiable through gsplat.rasterization.
    """
    from .rasterizer import make_view_matrix

    # Scene extent along the ortho camera's X and Y axes
    right_ax = R_c2w_ortho[:, 0]
    down_ax = R_c2w_ortho[:, 1]
    fwd_ax = R_c2w_ortho[:, 2]  # ≈ scene "down"

    corners = np.array([
        scene_lo,
        scene_hi,
        [scene_lo[0], scene_lo[1], scene_hi[2]],
        [scene_hi[0], scene_hi[1], scene_lo[2]],
        [scene_lo[0], scene_hi[1], scene_lo[2]],
        [scene_hi[0], scene_lo[1], scene_hi[2]],
        [scene_lo[0], scene_hi[1], scene_hi[2]],
        [scene_hi[0], scene_lo[1], scene_lo[2]],
    ])
    proj_r = corners @ right_ax
    proj_d = corners @ down_ax
    proj_f = corners @ fwd_ax

    r_min, r_max = proj_r.min(), proj_r.max()
    d_min, d_max = proj_d.min(), proj_d.max()
    f_min, f_max = proj_f.min(), proj_f.max()

    # Compute GSD from camera intrinsics: match the training image resolution
    # so the ortho crop can resolve the same features the cameras see.
    if cameras is not None and len(cameras) > 0:
        cam_locs = np.array([c.T for c in cameras])
        scene_center = (scene_lo + scene_hi) / 2.0
        avg_dist = np.linalg.norm(cam_locs - scene_center, axis=1).mean()
        avg_fx = np.mean([c.fx for c in cameras])
        gsd = avg_dist / avg_fx  # COLMAP-space GSD at full resolution
    else:
        scene_width = max(r_max - r_min, 1e-3)
        gsd = 0.15 * scene_width / crop_px

    crop_world = crop_px * gsd

    # Random crop centre (in projected camera-plane coordinates)
    margin = crop_world / 2.0
    r_lo = min(r_min + margin, r_max - margin)
    r_hi = max(r_min + margin, r_max - margin)
    d_lo = min(d_min + margin, d_max - margin)
    d_hi = max(d_min + margin, d_max - margin)
    rand_r = random.uniform(r_lo, r_hi)
    rand_d = random.uniform(d_lo, d_hi)

    # Camera position: world coordinates from projected camera-plane coords
    scene_center = (scene_lo + scene_hi) / 2.0
    r_center = (r_min + r_max) / 2.0
    d_center = (d_min + d_max) / 2.0
    cam_world = (scene_center
                 + (rand_r - r_center) * right_ax
                 + (rand_d - d_center) * down_ax)
    cam_world = cam_world + (f_max - f_min + 20.0) * (-fwd_ax)  # above scene

    half_w = crop_px * gsd / 2.0
    half_h = crop_px * gsd / 2.0
    fx = crop_px / (2.0 * half_w)
    fy = crop_px / (2.0 * half_h)

    viewmat = make_view_matrix(R_c2w_ortho, cam_world.astype(np.float32))
    K = torch.tensor([
        [fx, 0.0, crop_px / 2.0],
        [0.0, fy, crop_px / 2.0],
        [0.0, 0.0, 1.0],
    ], dtype=torch.float32, device=device).unsqueeze(0)
    vm = viewmat.to(device).unsqueeze(0)

    # --- Frustum culling: only send Gaussians overlapping the crop ---
    # Project Gaussian positions onto the camera-plane axes and keep
    # those within the crop region (+ scale-based margin).
    right_t = torch.tensor(right_ax, dtype=torch.float32, device=device)
    down_t = torch.tensor(down_ax, dtype=torch.float32, device=device)
    with torch.no_grad():
        xyz = splats["means"]  # (N, 3)
        proj_r_g = (xyz * right_t).sum(dim=1)  # (N,)
        proj_d_g = (xyz * down_t).sum(dim=1)   # (N,)
        # Margin: max Gaussian scale × 3σ
        gauss_margin = torch.exp(splats["scales"]).max(dim=1).values * 3.0
        cull_mask = (
            (proj_r_g + gauss_margin >= rand_r - crop_world / 2.0) &
            (proj_r_g - gauss_margin <= rand_r + crop_world / 2.0) &
            (proj_d_g + gauss_margin >= rand_d - crop_world / 2.0) &
            (proj_d_g - gauss_margin <= rand_d + crop_world / 2.0)
        )
        indices = cull_mask.nonzero(as_tuple=False).squeeze(-1)
        del proj_r_g, proj_d_g, gauss_margin, cull_mask

    if indices.numel() == 0:
        # No Gaussians in crop — return zero loss (no gradient)
        return torch.tensor(0.0, device=device, requires_grad=True)

    # Only pass DC band (sh0) — sh_degree=0 ignores higher bands anyway
    render_colors, render_alphas, _info = gsplat.rasterization(
        means=splats["means"][indices],
        quats=splats["quats"][indices],
        scales=torch.exp(splats["scales"][indices]),
        opacities=torch.sigmoid(splats["opacities"][indices]),
        colors=splats["colors"][indices, :1, :],   # DC band only
        viewmats=vm,
        Ks=K,
        width=crop_px,
        height=crop_px,
        near_plane=0.01,
        far_plane=1e10,
        sh_degree=0,       # DC only for speed
        eps2d=0.3,
        render_mode="RGB",
        rasterize_mode="antialiased",
        camera_model="ortho",
        absgrad=False,
        packed=True,
    )
    del indices

    alpha = render_alphas[0, :, :, 0]  # (H, W)

    # 1. Coverage loss: push alpha towards 1 everywhere
    coverage = ((1.0 - alpha) ** 2).mean()

    # 2. Row-uniformity loss: penalise row-to-row alpha variance
    #    (directly targets horizontal banding)
    row_mean = alpha.mean(dim=1)   # (H,)
    row_var = ((row_mean - row_mean.mean()) ** 2).mean()

    # 3. Total-variation smoothness
    tv = ((alpha[:, 1:] - alpha[:, :-1]).abs().mean()
          + (alpha[1:, :] - alpha[:-1, :]).abs().mean())

    return coverage + 2.0 * row_var + 0.5 * tv


# ---------------------------------------------------------------------------
#  Image loading with downscaling
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


def _get_current_data_factor(step: int, cfg: TrainConfig) -> int:
    """Return the data_factor for the current training step, respecting progressive schedule."""
    if not cfg.progressive_schedule:
        return cfg.data_factor
    # Schedule is [(fraction, df), ...] sorted by fraction ascending.
    current_df = cfg.progressive_schedule[0][1]
    frac = step / max(cfg.iterations - 1, 1)
    for threshold, df in cfg.progressive_schedule:
        if frac >= threshold:
            current_df = df
    return max(current_df, cfg.data_factor)  # never go below target data_factor


def _camera_data_for_factor(cam: CameraInfo, data_factor: int, device: torch.device) -> dict:
    """Build K and viewmat for a single camera at the given data_factor."""
    fx = cam.fx / data_factor if data_factor > 1 else cam.fx
    fy = cam.fy / data_factor if data_factor > 1 else cam.fy
    cx = cam.cx / data_factor if data_factor > 1 else cam.cx
    cy = cam.cy / data_factor if data_factor > 1 else cam.cy
    K = torch.tensor([
        [fx, 0.0, cx],
        [0.0, fy, cy],
        [0.0, 0.0, 1.0],
    ], dtype=torch.float32, device=device)
    R_w2c = cam.R.T
    t_w2c = -R_w2c @ cam.T
    vm = np.eye(4, dtype=np.float32)
    vm[:3, :3] = R_w2c
    vm[:3, 3] = t_w2c
    viewmat = torch.tensor(vm, dtype=torch.float32, device=device)
    return {"K": K, "viewmat": viewmat}


def _vram_mb():
    """Return current CUDA allocated memory in MB (0 if no GPU)."""
    if torch.cuda.is_available():
        return torch.cuda.memory_allocated() / (1024 ** 2)
    return 0.0


def _prune_splats(splats, keep_mask, optimizers, strategy_state, cfg, scene_scale):
    """Remove Gaussians where keep_mask is False.

    Rebuilds parameter dict, optimisers, and strategy state from scratch.
    Adam momentum is lost for the pruned Gaussians (acceptable — pruning
    is rare, ~2-4 times per training run).

    Returns (new_splats, new_optimizers, new_strategy_state).
    """
    device = splats["means"].device
    n_before = splats["means"].shape[0]
    n_after = int(keep_mask.sum().item())

    if n_after >= n_before:
        return splats, optimizers, strategy_state

    # Build new ParameterDict with only kept Gaussians
    new_splats = torch.nn.ParameterDict()
    with torch.no_grad():
        for name in splats.keys():
            new_splats[name] = torch.nn.Parameter(
                splats[name].data[keep_mask].clone()
            )

    # Carry over requires_grad settings
    for name in splats.keys():
        new_splats[name].requires_grad_(splats[name].requires_grad)

    new_splats = new_splats.to(device)

    # Delete old splats to free VRAM immediately
    del splats
    for opt in optimizers.values():
        opt.state.clear()
    del optimizers
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Rebuild optimizers
    new_optimizers = _create_optimizers(new_splats, cfg, scene_scale)

    # Reset strategy state
    new_strategy_state = {}

    return new_splats, new_optimizers, new_strategy_state


# ---------------------------------------------------------------------------
#  gsplat ParameterDict / optimisers
# ---------------------------------------------------------------------------

def _create_splats(model: GaussianModel, device: torch.device):
    """Convert GaussianModel parameters to gsplat ParameterDict format.

    SH coefficients are stored as a single contiguous 'colors' tensor
    (N, 1+K, 3) instead of separate sh0/shN.  This avoids a per-step
    torch.cat allocation that doubles SH peak VRAM during backward.
    At 4M Gaussians with SH degree 3 this saves ~1.5 GB.
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


def _create_optimizers(splats, cfg: TrainConfig, scene_scale: float):
    """Create per-parameter Adam optimisers (gsplat convention).

    For the merged 'colors' parameter, we use lr_shN (the lower LR)
    and compensate the DC band with gradient scaling in the training loop.
    """
    lr_map = {
        "means":     cfg.lr_means * scene_scale,
        "scales":    cfg.lr_scales,
        "opacities": cfg.lr_opacities,
        "quats":     cfg.lr_quats,
        "colors":    cfg.lr_shN,   # base LR; DC band scaled in training loop
    }
    # Use fused Adam when available (PyTorch 2.x) for better VRAM efficiency
    fused = torch.cuda.is_available()
    optimizers = {}
    for name, lr in lr_map.items():
        optimizers[name] = torch.optim.Adam(
            [{"params": splats[name], "lr": lr, "name": name}],
            eps=1e-15,
            betas=(0.9, 0.999),
            fused=fused,
        )
    return optimizers


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
        from .gaussian_model import num_sh_coefficients
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
    """Precompute viewmats and intrinsics for all training cameras.

    If norm_center and norm_scale are given, camera positions are transformed
    to normalised space:  pos_norm = (pos - center) / scale.
    Intrinsics (K) are unchanged because pixel projection is independent of
    world-space scaling.
    """
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
#  Main training loop
# ---------------------------------------------------------------------------

def train(scene: SceneInfo, model: GaussianModel, cfg: TrainConfig = None,
          report_fn=None):
    """
    Train a 3DGS model using gsplat's DefaultStrategy for densification.

    Parameters
    ----------
    scene : SceneInfo
        Scene data (cameras, point cloud, bounds).
    model : GaussianModel
        Gaussian model (already initialised from point cloud).
    cfg : TrainConfig
        Training hyperparameters.
    report_fn : callable, optional
        report_fn(iteration, loss, n_gaussians) for logging.

    Returns
    -------
    model : GaussianModel
    """
    if cfg is None:
        cfg = TrainConfig()

    torch.manual_seed(cfg.seed)
    random.seed(cfg.seed)
    np.random.seed(cfg.seed)

    device = model.positions.device
    os.makedirs(cfg.output_dir, exist_ok=True)

    cameras = scene.train_cameras

    # ------------------------------------------------------------------
    #  Scene normalisation: centre at scene_centre, scale to unit radius.
    #  gsplat's default MCMC hyperparameters (noise_lr, LR values, etc.)
    #  are tuned for scenes where positions live in a ~unit ball.  Our raw
    #  COLMAP coordinates can be 10-30 units across (1 unit ≈ 12 m), which
    #  breaks noise injection scaling.  By normalising here we can use all
    #  upstream defaults unmodified and convert back after training.
    # ------------------------------------------------------------------
    norm_center = scene.scene_centre.astype(np.float64)
    norm_scale = float(scene.scene_radius)          # radius → ~1.0
    if norm_scale < 1e-6:
        norm_scale = 1.0

    # scene_scale (for LR) is now just a small padding factor (~1.1)
    scene_scale = 1.1

    # --- Create gsplat params, optimisers, strategy ---
    splats = _create_splats(model, device)

    # Free the original model's GPU tensors — splats has its own copies.
    # This saves ~30% of Gaussian VRAM (model params are dead weight during training).
    model.to("cpu")

    # Normalise Gaussian positions and scales into the unit-ball space.
    with torch.no_grad():
        splats["means"].sub_(torch.tensor(norm_center, dtype=torch.float32,
                                          device=device))
        splats["means"].div_(norm_scale)
        # Scales are in log-space: log(s_world) → log(s_world / norm_scale)
        splats["scales"].sub_(math.log(norm_scale))

    print(f"Scene normalisation: center={norm_center}, "
          f"scale={norm_scale:.4f} (1 unit ≈ {norm_scale:.2f} COLMAP units)")

    optimizers = _create_optimizers(splats, cfg, scene_scale)

    # ExponentialLR: means LR decays to 1% over training
    schedulers = [
        torch.optim.lr_scheduler.ExponentialLR(
            optimizers["means"], gamma=0.01 ** (1.0 / cfg.iterations)
        ),
    ]

    # Strategy
    use_mcmc = cfg.strategy.lower() == "mcmc"
    if use_mcmc:
        strategy = MCMCStrategy(
            verbose=True,
            cap_max=cfg.cap_max,
            noise_lr=cfg.noise_lr,
            refine_start_iter=cfg.refine_start_iter,
            # Stop refinement at 75% of training to leave a settling phase
            # where Gaussians converge without being relocated/added.
            refine_stop_iter=min(cfg.refine_stop_iter, int(cfg.iterations * 0.75)),
            refine_every=cfg.refine_every,
            min_opacity=0.005,
        )
    else:
        strategy = DefaultStrategy(
            verbose=True,
            absgrad=True,
            refine_start_iter=cfg.refine_start_iter,
            refine_stop_iter=min(cfg.refine_stop_iter, cfg.iterations),
            refine_every=cfg.refine_every,
            reset_every=cfg.reset_every,
        )
    strategy.check_sanity(splats, optimizers)
    if use_mcmc:
        strategy_state = strategy.initialize_state()
    else:
        strategy_state = strategy.initialize_state(scene_scale=scene_scale)

    # Precompute camera data (in normalised space)
    cam_data = _precompute_cameras(cameras, cfg.data_factor, device,
                                   norm_center=norm_center,
                                   norm_scale=norm_scale)

    bg_color = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float32, device=device)

    # Per-image appearance model for exposure/WB compensation
    app_model = None
    app_optimizer = None
    if cfg.appearance_enabled and len(cameras) > 1:
        app_model = AppearanceModel(len(cameras), cfg.appearance_dim).to(device)
        app_optimizer = torch.optim.Adam(app_model.parameters(), lr=cfg.appearance_lr)

    print(f"Training: {cfg.iterations} iters, {len(cameras)} cameras, "
          f"data_factor={cfg.data_factor}, {splats['means'].shape[0]} initial GS"
          f", appearance={'on' if app_model else 'off'}"
          f"{', progressive' if cfg.progressive_schedule else ''}")

    # --- Spatial pruning setup (camera proximity tree, normalised space) ---
    _cam_positions = None
    _cam_tree = None
    _max_cam_dist = None
    if use_mcmc and cfg.spatial_prune_every > 0:
        from scipy.spatial import cKDTree as _cKDTree
        from scipy.spatial.distance import pdist as _pdist
        # Camera positions in normalised space
        _cam_positions = np.array(
            [(cam.T - norm_center) / norm_scale for cam in cameras],
            dtype=np.float64)
        _cam_tree = _cKDTree(_cam_positions)
        _max_cam_dist = float(np.max(_pdist(_cam_positions)))
        print(f"Spatial pruning: enabled every {cfg.spatial_prune_every} steps, "
              f"scene diameter={_max_cam_dist:.2f} normalised units")

    # --- Ortho coverage regularisation setup (normalised space) ---
    ortho_R_c2w = None
    ortho_scene_lo = ortho_scene_hi = None
    if cfg.ortho_reg > 0:
        ortho_R_c2w = _estimate_nadir_frame(cameras)
        pts = scene.point_cloud.points
        lo = np.percentile(pts, 2, axis=0)
        hi = np.percentile(pts, 98, axis=0)
        ortho_scene_lo = (lo - norm_center) / norm_scale
        ortho_scene_hi = (hi - norm_center) / norm_scale
        print(f"Ortho reg enabled: weight={cfg.ortho_reg}, every {cfg.ortho_reg_every} "
              f"iters from {cfg.ortho_reg_start}, crop={cfg.ortho_crop_px}px")

    # Pre-compute camera data at the initial data_factor (normalised space)
    current_df = _get_current_data_factor(0, cfg)
    cam_data = _precompute_cameras(cameras, current_df, device,
                                   norm_center=norm_center,
                                   norm_scale=norm_scale)

    # --- Resolution-proportional cap: prevent MCMC over-growing at low res ---
    # At low resolution (df=8, df=4), the pixel budget can't support many
    # Gaussians.  MCMC will grow to cap_max regardless, creating a death
    # spiral where most Gaussians are dead and constantly relocated.
    # Scale cap_max proportionally to current resolution vs target resolution.
    _target_pixels = (cameras[0].width // cfg.data_factor) * \
                     (cameras[0].height // cfg.data_factor)
    _original_cap = cfg.cap_max

    def _update_cap_for_resolution(cur_df):
        """Scale strategy.cap_max based on current resolution."""
        if not (use_mcmc and cfg.progressive_schedule):
            return
        cur_pixels = (cameras[0].width // cur_df) * (cameras[0].height // cur_df)
        # 0.5 GS/pixel is a healthy maximum; scale linearly with resolution
        effective_cap = max(int(_original_cap * cur_pixels / _target_pixels),
                           100_000)  # minimum 100K
        if effective_cap != strategy.cap_max:
            print(f"  Cap scaled for df={cur_df}: {strategy.cap_max:,} → "
                  f"{effective_cap:,} ({cur_pixels:,} px)")
        strategy.cap_max = effective_cap

    _update_cap_for_resolution(current_df)

    for step in range(cfg.iterations):
        # --- Progressive resolution: update data_factor if needed ---
        step_df = _get_current_data_factor(step, cfg)
        if step_df != current_df:
            # Resolution is increasing — flush VRAM before allocating larger tensors.
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            n_gs = splats["means"].shape[0]
            print(f"  Progressive: data_factor {current_df}→{step_df} at step {step} "
                  f"(N={n_gs:,}, VRAM={_vram_mb():.0f} MB)")

            # Update cap for the NEW resolution BEFORE pruning, so pruning
            # uses the correct target count for the incoming resolution.
            _update_cap_for_resolution(step_df)

            # --- Safe pre-resolution pruning (MCMC only, resolution increasing) ---
            # Uses gsplat's remove() which preserves Adam optimizer state for
            # surviving Gaussians — no momentum reset, no death spiral.
            if use_mcmc and step_df < current_df:
                from gsplat.strategy.ops import remove as _gsplat_remove

                # Use the proportional cap for the new resolution as the target.
                # Also check VRAM safety: if the estimated peak exceeds GPU
                # capacity, further reduce the cap.
                safe_cap = strategy.cap_max
                if torch.cuda.is_available():
                    total_memory = torch.cuda.get_device_properties(0).total_memory
                    peak_mem = torch.cuda.max_memory_allocated()
                    n_now = splats["means"].shape[0]
                    pixel_ratio = (current_df / step_df) ** 2
                    estimated_peak = peak_mem * (pixel_ratio ** 0.6)
                    target = 0.75 * total_memory
                    if estimated_peak > target and n_now > 0:
                        scale = target / estimated_peak
                        vram_cap = max(100_000, int(n_now * scale))
                        if vram_cap < safe_cap:
                            safe_cap = vram_cap
                            strategy.cap_max = safe_cap
                            print(f"  VRAM-safe cap: → {safe_cap:,} "
                                  f"(peak={peak_mem/1024**2:.0f}MB, "
                                  f"est@df{step_df}={estimated_peak/1024**2:.0f}MB, "
                                  f"GPU={total_memory/1024**2:.0f}MB)")
                    # Reset peak stats so next transition uses new-resolution data
                    torch.cuda.reset_peak_memory_stats()

                # Only prune if N exceeds the target cap for the new resolution.
                # Uses gsplat's remove() which preserves optimizer state.
                n_now = splats["means"].shape[0]
                if n_now > safe_cap:
                    with torch.no_grad():
                        opa = torch.sigmoid(splats["opacities"])
                        n_to_remove = n_now - safe_cap
                        _, remove_idx = torch.topk(opa, n_to_remove, largest=False)
                        remove_mask = torch.zeros(n_now, dtype=torch.bool, device=device)
                        remove_mask[remove_idx] = True
                        _gsplat_remove(
                            params=splats, optimizers=optimizers,
                            state={}, mask=remove_mask,
                        )
                        print(f"  Pre-res prune: {n_now:,} → "
                              f"{splats['means'].shape[0]:,} "
                              f"(removed {n_to_remove:,} lowest-opacity GS)")
                        del opa, remove_idx, remove_mask
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

            current_df = step_df
            cam_data = _precompute_cameras(cameras, current_df, device,
                                           norm_center=norm_center,
                                           norm_scale=norm_scale)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        # Random camera
        idx = random.randint(0, len(cameras) - 1)
        cd = cam_data[idx]
        cam = cameras[idx]

        # Load image (streamed from disk, not cached — saves VRAM)
        pixels = _load_image(cam, current_df, device)  # (1, H, W, 3)
        height, width = pixels.shape[1], pixels.shape[2]

        # Progressive SH degree
        sh_degree = min(step // cfg.sh_degree_interval, cfg.sh_degree)

        # --- Forward (gsplat rasterisation) ---
        render_colors, render_alphas, info = gsplat.rasterization(
            means=splats["means"],
            quats=splats["quats"],
            scales=torch.exp(splats["scales"]),
            opacities=torch.sigmoid(splats["opacities"]),
            colors=splats["colors"],   # merged SH (N, 1+K, 3) — no cat()
            viewmats=cd["viewmat"].unsqueeze(0),
            Ks=cd["K"].unsqueeze(0),
            width=width,
            height=height,
            near_plane=0.01,
            far_plane=1e10,
            sh_degree=sh_degree,
            eps2d=0.3,
            render_mode="RGB",
            rasterize_mode="classic",
            absgrad=(not use_mcmc),
            packed=True,
        )

        # Background compositing — modulated backgrounds prevent floaters
        # by making semi-transparent Gaussians unable to exploit a constant BG.
        use_random_bg = (
            cfg.random_bkgd
            and step < int(cfg.iterations * cfg.random_bkgd_end_frac)
        )
        if use_random_bg:
            bkgd = torch.rand(1, 3, device=device)
        else:
            bkgd = bg_color.unsqueeze(0)
        colors = render_colors + bkgd.view(1, 1, 1, 3) * (1.0 - render_alphas)

        # Per-image appearance correction (decouples exposure from scene colour).
        # Applied to the rendered image so the Gaussians learn canonical scene
        # colours directly.  The appearance model maps rendered→GT space,
        # absorbing per-image exposure/WB variations.  Scale is constrained to
        # [0.8, 1.2] to prevent degenerate solutions where the model collapses
        # GT signal to zero.
        if app_model is not None:
            colors = app_model(idx, colors)

        # Strategy pre-backward (no-op for DefaultStrategy, but keeps API correct)
        strategy.step_pre_backward(
            params=splats, optimizers=optimizers,
            state=strategy_state, step=step, info=info,
        )

        # --- Loss ---
        l1loss = F.l1_loss(colors, pixels)
        ssimloss = dssim_loss(
            colors.permute(0, 3, 1, 2), pixels.permute(0, 3, 1, 2)
        )
        loss = (1.0 - cfg.ssim_lambda) * l1loss + cfg.ssim_lambda * ssimloss

        # Regularisation (important for MCMC)
        if cfg.opacity_reg > 0.0:
            loss = loss + cfg.opacity_reg * torch.sigmoid(splats["opacities"]).mean()
        if cfg.scale_reg > 0.0:
            loss = loss + cfg.scale_reg * torch.exp(splats["scales"]).mean()

        # --- Backward pass 1: photometric loss ---
        # Backward the main loss FIRST so gsplat's rasterization graph is freed
        # before computing the ortho coverage loss (which runs its own rasterization).
        # This halves peak VRAM vs. a single combined backward.
        last_loss = l1loss.item()
        loss.backward()

        # Scale DC-band gradient to achieve effective lr_sh0
        # (colors uses lr_shN as base; DC band needs higher LR)
        with torch.no_grad():
            g = splats["colors"].grad
            if g is not None:
                g[:, :1, :] *= cfg.lr_sh0 / max(cfg.lr_shN, 1e-10)

        del pixels, render_colors, render_alphas, colors, loss
        del l1loss, ssimloss

        # Free rasterisation intermediates early for MCMC — step_post_backward
        # never reads `info` (only DefaultStrategy uses radii from it).
        # This frees ~500 MB of tile intersection buffers (isect_ids,
        # flatten_ids, gaussian_ids, means2d, conics) before MCMC allocates
        # memory for relocation / addition.
        if use_mcmc:
            del info

        # --- Backward pass 2: ortho coverage regularisation (separate graph) ---
        if (cfg.ortho_reg > 0
                and step >= cfg.ortho_reg_start
                and step % cfg.ortho_reg_every == 0):
            ortho_loss = _ortho_coverage_loss(
                splats, ortho_R_c2w, ortho_scene_lo, ortho_scene_hi,
                cfg.ortho_crop_px, device, cameras=cameras,
            )
            (cfg.ortho_reg * ortho_loss).backward()
            del ortho_loss

        # --- Optimise ---
        for optimizer in optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        if app_optimizer is not None:
            app_optimizer.step()
            app_optimizer.zero_grad(set_to_none=True)

        # Cap maximum scale to prevent tile-intersection explosion.
        if cfg.max_scale > 0:
            with torch.no_grad():
                splats["scales"].data.clamp_(max=math.log(cfg.max_scale))
        for scheduler in schedulers:
            scheduler.step()

        # --- Strategy post-backward (densification / relocation) ---
        # Note: MCMC's step_post_backward ignores `info` and has its own
        # empty_cache() after relocation.  For DefaultStrategy, `info` holds
        # radii needed for densification so it must stay alive.
        if use_mcmc:
            mcmc_lr = schedulers[0].get_last_lr()[0]
            if step >= strategy.refine_stop_iter:
                mcmc_lr = 0.0
            strategy.step_post_backward(
                params=splats, optimizers=optimizers,
                state=strategy_state, step=step, info={},
                lr=mcmc_lr,
            )
        else:
            strategy.step_post_backward(
                params=splats, optimizers=optimizers,
                state=strategy_state, step=step, info=info,
                packed=True,
            )

        # --- Spatial pruning: mark far-away Gaussians as dead ---
        # Crush opacity so MCMC's relocation mechanism naturally recycles
        # them to useful positions near cameras.  We do NOT remove them
        # (that would reset optimizer state and cause a death spiral).
        if (_cam_tree is not None
                and step >= cfg.refine_start_iter
                and step < strategy.refine_stop_iter
                and step % cfg.spatial_prune_every == 0):
            with torch.no_grad():
                means_np = splats["means"].detach().cpu().numpy()
                dists_to_cam, _ = _cam_tree.query(means_np, k=1)
                far_mask_np = dists_to_cam > _max_cam_dist
                n_far = int(far_mask_np.sum())
                del means_np, dists_to_cam

                if n_far > 0:
                    far_t = torch.tensor(far_mask_np, device=device)
                    # Crush opacity to near-zero so MCMC will relocate them
                    splats["opacities"].data[far_t] = torch.logit(
                        torch.tensor(0.001, device=device))
                    # Shrink scales so they don't pollute renders
                    splats["scales"].data[far_t] = math.log(1e-4)
                    del far_t

                if n_far > 100 or step % 500 == 0:
                    print(f"  [step {step}] Spatial prune: crushed {n_far} far "
                          f"Gaussians (N={splats['means'].shape[0]:,}), "
                          f"VRAM={_vram_mb():.0f} MB")
                del far_mask_np

        # --- Free per-iteration intermediates ---
        # For DefaultStrategy, `info` was kept alive through step_post_backward;
        # for MCMC it was already deleted right after loss.backward().
        if not use_mcmc:
            del info

        # --- Logging ---
        if report_fn and step % 100 == 0:
            report_fn(step, last_loss, len(splats["means"]))
        if step % 500 == 0 and device.type == "cuda":
            peak_mb = torch.cuda.max_memory_allocated() / 1024**2
            max_s = torch.exp(splats["scales"]).max().item()
            print(f"  [step {step}] VRAM: {_vram_mb():.0f} MB allocated, "
                  f"peak={peak_mb:.0f} MB, "
                  f"N={splats['means'].shape[0]:,}, max_scale={max_s:.4f}")
            torch.cuda.reset_peak_memory_stats()

        # --- Checkpoint (save in COLMAP coordinates) ---
        if (step + 1) in cfg.checkpoint_iterations:
            with torch.no_grad():
                # Temporarily denormalise for saving
                splats["means"].mul_(norm_scale).add_(
                    torch.tensor(norm_center, dtype=torch.float32, device=device))
                splats["scales"].add_(math.log(norm_scale))
                _update_model_from_splats(model, splats)
                ckpt_path = os.path.join(cfg.output_dir, f"point_cloud_iter{step + 1}.ply")
                model.save_ply(ckpt_path)
                # Re-normalise to continue training
                splats["means"].sub_(
                    torch.tensor(norm_center, dtype=torch.float32, device=device))
                splats["means"].div_(norm_scale)
                splats["scales"].sub_(math.log(norm_scale))
            if report_fn:
                report_fn(step, last_loss, len(splats["means"]))

    # --- Denormalise back to COLMAP coordinates ---
    # Training was done in unit-ball space.  Convert means and scales back
    # so the exported model lives in the original COLMAP coordinate frame
    # (needed for correct GeoTIFF placement and metric measurements).
    with torch.no_grad():
        splats["means"].mul_(norm_scale).add_(
            torch.tensor(norm_center, dtype=torch.float32, device=device))
        splats["scales"].add_(math.log(norm_scale))

    # Copy final results back to model
    _update_model_from_splats(model, splats)
    model.active_sh_degree = min(cfg.iterations // cfg.sh_degree_interval, cfg.sh_degree)

    # --- Free training memory ---
    del splats, optimizers, schedulers, strategy, strategy_state
    del cam_data, bg_color, app_model, app_optimizer
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    import gc; gc.collect()

    return model


# ---------------------------------------------------------------------------
#  Nadir fine-tune: optimise colours for top-down rendering
# ---------------------------------------------------------------------------

def _filter_nadir_cameras(cameras, max_angle_deg: float = 15.0):
    """Return cameras whose viewing direction is within *max_angle_deg* of
    the PCA-derived vertical (nadir) direction.

    Camera forward in world coords = ``cam.R[:, 2]`` (3rd column of the
    camera-to-world rotation matrix — the camera +Z axis in COLMAP
    convention).
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


def nadir_finetune(scene: SceneInfo, model: GaussianModel,
                   iterations: int = 2000,
                   data_factor: int = 2,
                   max_angle_deg: float = 15.0,
                   lr_sh0: float = 2.5e-3,
                   lr_shN: float = 1.25e-4,
                   ssim_lambda: float = 0.2,
                   report_fn=None):
    """Fine-tune only the SH colour coefficients using near-nadir cameras.

    The geometry (positions, scales, rotations, opacities) is frozen.
    This makes the Gaussian colours match the straight-down view used
    by the orthographic renderer, eliminating the colour mismatch that
    arises when SH bands are trained from mixed oblique + nadir views.

    Parameters
    ----------
    scene : SceneInfo
        Same scene used for the original training.
    model : GaussianModel
        Already-trained model (with good geometry).
    iterations : int
        Number of fine-tune iterations.
    data_factor : int
        Image downscaling factor.
    max_angle_deg : float
        Maximum angle from nadir for camera selection.
    lr_sh0 : float
        Learning rate for the DC (base colour) SH band.
    lr_shN : float
        Learning rate for higher SH bands.
    ssim_lambda : float
        SSIM loss weight (0 = pure L1, 1 = pure SSIM).
    report_fn : callable, optional
        report_fn(iteration, loss, n_gaussians) for progress.
    """
    device = model.positions.device

    # --- Filter to nadir cameras ---
    nadir_cams, down_dir = _filter_nadir_cameras(scene.train_cameras,
                                                  max_angle_deg)
    if len(nadir_cams) < 10:
        print(f"WARNING: only {len(nadir_cams)} nadir cameras (<{max_angle_deg}°),"
              f" falling back to 30° threshold")
        nadir_cams, down_dir = _filter_nadir_cameras(scene.train_cameras, 30.0)
    if len(nadir_cams) < 5:
        print(f"ERROR: only {len(nadir_cams)} cameras even at 30° — skipping finetune")
        return model

    print(f"Nadir fine-tune: {len(nadir_cams)} cameras (<{max_angle_deg}°), "
          f"{iterations} iters, data_factor={data_factor}")

    # --- Scene normalisation (same as main training) ---
    norm_center = scene.scene_centre.astype(np.float64)
    norm_scale = float(scene.scene_radius)
    if norm_scale < 1e-6:
        norm_scale = 1.0

    # --- Build splats from existing model ---
    splats = _create_splats(model, device)

    # Normalise positions and scales
    with torch.no_grad():
        splats["means"].sub_(torch.tensor(norm_center, dtype=torch.float32,
                                          device=device))
        splats["means"].div_(norm_scale)
        splats["scales"].sub_(math.log(norm_scale))

    # --- Freeze geometry: only optimise SH coefficients ---
    splats["means"].requires_grad_(False)
    splats["scales"].requires_grad_(False)
    splats["quats"].requires_grad_(False)
    splats["opacities"].requires_grad_(False)

    dc_lr_scale = lr_sh0 / max(lr_shN, 1e-10)
    fused = torch.cuda.is_available()
    optimizers = {
        "colors": torch.optim.Adam([splats["colors"]], lr=lr_shN, eps=1e-15, fused=fused),
    }

    # Precompute camera data (normalised space)
    cam_data = _precompute_cameras(nadir_cams, data_factor, device,
                                   norm_center=norm_center,
                                   norm_scale=norm_scale)

    bg_color = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float32, device=device)
    sh_degree = model.max_sh_degree  # Use full SH from the start

    # --- Training loop ---
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
            colors=splats["colors"],   # merged SH — no cat()
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

        # Loss
        l1loss = F.l1_loss(colors, pixels)
        ssimloss = dssim_loss(
            colors.permute(0, 3, 1, 2), pixels.permute(0, 3, 1, 2)
        )
        loss = (1.0 - ssim_lambda) * l1loss + ssim_lambda * ssimloss

        loss.backward()

        # Scale DC gradient for effective lr_sh0
        with torch.no_grad():
            g = splats["colors"].grad
            if g is not None:
                g[:, :1, :] *= dc_lr_scale

        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)

        if report_fn and step % 100 == 0:
            report_fn(step, l1loss.item(), len(splats["means"]))

        del pixels, render_colors, render_alphas, colors, info
        del l1loss, ssimloss, loss

        if step % 100 == 0 and device.type == "cuda":
            torch.cuda.empty_cache()

    # --- Denormalise and copy back ---
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

    print(f"Nadir fine-tune complete.")
    return model


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

    Like :func:`nadir_finetune` but also optimises scales and opacity so that
    Gaussians can adapt their shapes for the nadir ortho view:

    - Large "fill" Gaussians can shrink for sharper orthographic rendering
    - Opacity can be refined for cleaner alpha compositing
    - Positions and rotations stay **frozen** (no geometric collapse)

    Parameters
    ----------
    scene : SceneInfo
        Same scene used for the original training.
    model : GaussianModel
        Already-trained model (with good geometry).
    iterations : int
        Number of fine-tune iterations.
    data_factor : int
        Image downscaling factor.
    max_angle_deg : float
        Maximum angle from nadir for camera selection.
    lr_sh0, lr_shN : float
        Learning rates for SH bands.
    lr_scales : float
        Learning rate for Gaussian scales.
    lr_opacities : float
        Learning rate for Gaussian opacities.
    ssim_lambda : float
        SSIM loss weight (0 = pure L1, 1 = pure SSIM).
    report_fn : callable, optional
        report_fn(iteration, loss, n_gaussians) for progress.
    """
    device = model.positions.device

    nadir_cams, down_dir = _filter_nadir_cameras(scene.train_cameras,
                                                  max_angle_deg)
    if len(nadir_cams) < 10:
        print(f"WARNING: only {len(nadir_cams)} nadir cameras (<{max_angle_deg}°),"
              f" falling back to 30° threshold")
        nadir_cams, down_dir = _filter_nadir_cameras(scene.train_cameras, 30.0)
    if len(nadir_cams) < 5:
        print(f"ERROR: only {len(nadir_cams)} cameras even at 30° — skipping finetune")
        return model

    print(f"Nadir fine-tune (full): {len(nadir_cams)} cameras (<{max_angle_deg}°), "
          f"{iterations} iters, data_factor={data_factor}")
    print(f"  Optimising: colors, scales, opacities (positions/rotations frozen)")

    norm_center = scene.scene_centre.astype(np.float64)
    norm_scale = float(scene.scene_radius)
    if norm_scale < 1e-6:
        norm_scale = 1.0

    splats = _create_splats(model, device)

    with torch.no_grad():
        splats["means"].sub_(torch.tensor(norm_center, dtype=torch.float32,
                                          device=device))
        splats["means"].div_(norm_scale)
        splats["scales"].sub_(math.log(norm_scale))

    # Freeze positions and rotations only
    splats["means"].requires_grad_(False)
    splats["quats"].requires_grad_(False)
    # Unfreeze scales and opacities (in addition to SH)
    splats["scales"].requires_grad_(True)
    splats["opacities"].requires_grad_(True)

    dc_lr_scale = lr_sh0 / max(lr_shN, 1e-10)
    fused = torch.cuda.is_available()
    optimizers = {
        "colors":    torch.optim.Adam([splats["colors"]], lr=lr_shN, eps=1e-15, fused=fused),
        "scales":    torch.optim.Adam([splats["scales"]], lr=lr_scales, eps=1e-15, fused=fused),
        "opacities": torch.optim.Adam([splats["opacities"]], lr=lr_opacities, eps=1e-15, fused=fused),
    }

    cam_data = _precompute_cameras(nadir_cams, data_factor, device,
                                   norm_center=norm_center,
                                   norm_scale=norm_scale)

    bg_color = torch.tensor([1.0, 1.0, 1.0], dtype=torch.float32, device=device)
    sh_degree = model.max_sh_degree

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
            colors=splats["colors"],   # merged SH — no cat()
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

        # Scale DC gradient for effective lr_sh0
        with torch.no_grad():
            g = splats["colors"].grad
            if g is not None:
                g[:, :1, :] *= dc_lr_scale

        for opt in optimizers.values():
            opt.step()
            opt.zero_grad(set_to_none=True)

        if report_fn and step % 100 == 0:
            report_fn(step, l1loss.item(), len(splats["means"]))

        del pixels, render_colors, render_alphas, colors, info
        del l1loss, ssimloss, loss

        if step % 100 == 0 and device.type == "cuda":
            torch.cuda.empty_cache()

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

    print(f"Nadir fine-tune (full) complete.")
    return model
