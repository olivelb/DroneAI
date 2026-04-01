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
    refine_stop_iter: int = 15_000
    refine_every: int = 100
    reset_every: int = 3000
    # MCMCStrategy
    cap_max: int = 1_000_000
    noise_lr: float = 5e5
    # Regularisation (used with MCMC)
    opacity_reg: float = 0.01
    scale_reg: float = 0.01
    # Initial opacity (0.1 for default, 0.5 for mcmc)
    init_opa: float = 0.1
    # Random background during training (helps avoid transparency)
    random_bkgd: bool = False
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
    # Ortho coverage regularisation (removes nadir banding artefacts)
    ortho_reg: float = 0.5       # weight (0 to disable)
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
    scale and bias.  At ortho-render time this module is NOT used, so the
    Gaussians learn the *canonical* scene colour and flight-strip banding
    disappears.
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
        """Apply per-image colour correction.

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
        scale = torch.sigmoid(params[:3]) * 2.0  # range (0, 2)
        bias = params[3:] * 0.1  # small bias range
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

    colors_sh = torch.cat([splats["sh0"], splats["shN"]], dim=1)

    render_colors, render_alphas, _info = gsplat.rasterization(
        means=splats["means"],
        quats=splats["quats"],
        scales=torch.exp(splats["scales"]),
        opacities=torch.sigmoid(splats["opacities"]),
        colors=colors_sh,
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


# ---------------------------------------------------------------------------
#  gsplat ParameterDict / optimisers
# ---------------------------------------------------------------------------

def _create_splats(model: GaussianModel, device: torch.device):
    """Convert GaussianModel parameters to gsplat ParameterDict format."""
    return torch.nn.ParameterDict({
        "means":     torch.nn.Parameter(model._xyz.data.clone()),
        "scales":    torch.nn.Parameter(model._scaling.data.clone()),
        "quats":     torch.nn.Parameter(model._rotation.data.clone()),
        "opacities": torch.nn.Parameter(model._opacity.data.clone().squeeze(-1)),  # (N,)
        "sh0":       torch.nn.Parameter(model._features_dc.data.clone()),   # (N, 1, 3)
        "shN":       torch.nn.Parameter(model._features_rest.data.clone()), # (N, K, 3)
    }).to(device)


def _create_optimizers(splats, cfg: TrainConfig, scene_scale: float):
    """Create per-parameter Adam optimisers (gsplat convention)."""
    lr_map = {
        "means":     cfg.lr_means * scene_scale,
        "scales":    cfg.lr_scales,
        "opacities": cfg.lr_opacities,
        "quats":     cfg.lr_quats,
        "sh0":       cfg.lr_sh0,
        "shN":       cfg.lr_shN,
    }
    optimizers = {}
    for name, lr in lr_map.items():
        optimizers[name] = torch.optim.Adam(
            [{"params": splats[name], "lr": lr, "name": name}],
            eps=1e-15,
            betas=(0.9, 0.999),
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
    model._features_dc = nn.Parameter(splats["sh0"].data)
    model._features_rest = nn.Parameter(splats["shN"].data)

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

def _precompute_cameras(cameras, data_factor: int, device: torch.device):
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

        R_w2c = cam.R.T
        t_w2c = -R_w2c @ cam.T
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
    scene_scale = scene.scene_radius * 1.1

    # --- Create gsplat params, optimisers, strategy ---
    splats = _create_splats(model, device)
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
            refine_stop_iter=min(cfg.refine_stop_iter, cfg.iterations),
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

    # Precompute camera data
    cam_data = _precompute_cameras(cameras, cfg.data_factor, device)

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

    # --- Ortho coverage regularisation setup ---
    ortho_R_c2w = None
    ortho_scene_lo = ortho_scene_hi = None
    if cfg.ortho_reg > 0:
        ortho_R_c2w = _estimate_nadir_frame(cameras)
        pts = scene.point_cloud.points
        ortho_scene_lo = np.percentile(pts, 2, axis=0)
        ortho_scene_hi = np.percentile(pts, 98, axis=0)
        print(f"Ortho reg enabled: weight={cfg.ortho_reg}, every {cfg.ortho_reg_every} "
              f"iters from {cfg.ortho_reg_start}, crop={cfg.ortho_crop_px}px")

    # Pre-compute camera data at the initial data_factor
    current_df = _get_current_data_factor(0, cfg)
    cam_data = _precompute_cameras(cameras, current_df, device)

    for step in range(cfg.iterations):
        # --- Progressive resolution: update data_factor if needed ---
        step_df = _get_current_data_factor(step, cfg)
        if step_df != current_df:
            current_df = step_df
            cam_data = _precompute_cameras(cameras, current_df, device)
            if step % 100 == 0:
                print(f"  Progressive: data_factor → {current_df} at step {step}")

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
        colors_sh = torch.cat([splats["sh0"], splats["shN"]], dim=1)

        render_colors, render_alphas, info = gsplat.rasterization(
            means=splats["means"],
            quats=splats["quats"],
            scales=torch.exp(splats["scales"]),
            opacities=torch.sigmoid(splats["opacities"]),
            colors=colors_sh,
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
            absgrad=(not use_mcmc),
            packed=True,
        )

        # Background compositing
        if cfg.random_bkgd:
            bkgd = torch.rand(1, 3, device=device)
        else:
            bkgd = bg_color.unsqueeze(0)
        colors = render_colors + bkgd.view(1, 1, 1, 3) * (1.0 - render_alphas)

        # Per-image appearance correction (decouples exposure from scene colour)
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

        # Ortho coverage regularisation
        if (cfg.ortho_reg > 0
                and step >= cfg.ortho_reg_start
                and step % cfg.ortho_reg_every == 0):
            ortho_loss = _ortho_coverage_loss(
                splats, ortho_R_c2w, ortho_scene_lo, ortho_scene_hi,
                cfg.ortho_crop_px, device, cameras=cameras,
            )
            loss = loss + cfg.ortho_reg * ortho_loss

        loss.backward()

        # --- Optimise ---
        for optimizer in optimizers.values():
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        if app_optimizer is not None:
            app_optimizer.step()
            app_optimizer.zero_grad(set_to_none=True)
        for scheduler in schedulers:
            scheduler.step()

        # --- Strategy post-backward (densification / relocation) ---
        if use_mcmc:
            strategy.step_post_backward(
                params=splats, optimizers=optimizers,
                state=strategy_state, step=step, info=info,
                lr=schedulers[0].get_last_lr()[0],
            )
        else:
            strategy.step_post_backward(
                params=splats, optimizers=optimizers,
                state=strategy_state, step=step, info=info,
                packed=True,
            )

        # --- Free per-iteration intermediates (reduce peak VRAM) ---
        last_loss = l1loss.item()
        del pixels, render_colors, render_alphas, colors, colors_sh, info, loss
        del l1loss, ssimloss
        if step % 100 == 0 and device.type == "cuda":
            torch.cuda.empty_cache()

        # --- Logging ---
        if report_fn and step % 100 == 0:
            report_fn(step, last_loss, len(splats["means"]))

        # --- Checkpoint ---
        if (step + 1) in cfg.checkpoint_iterations:
            _update_model_from_splats(model, splats)
            ckpt_path = os.path.join(cfg.output_dir, f"point_cloud_iter{step + 1}.ply")
            model.save_ply(ckpt_path)
            if report_fn:
                report_fn(step, last_loss, len(splats["means"]))

    # Copy final results back to model
    _update_model_from_splats(model, splats)

    # --- Free training memory ---
    del splats, optimizers, schedulers, strategy, strategy_state
    del cam_data, bg_color, app_model, app_optimizer
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    import gc; gc.collect()

    return model
