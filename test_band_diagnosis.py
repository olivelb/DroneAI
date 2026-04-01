"""
Deep diagnostic for Gaussian ortho banding.

Tests:
1. Alpha-channel analysis: are bands in opacity/coverage or in color?
2. Gaussian Y-coordinate distribution: is there periodicity in 3D positions?
3. Perspective vs Ortho: same checkpoint, rendered from a perspective cam vs ortho
4. Row-by-row analysis: mean alpha and mean RGB per row
"""
import sys, os, json
sys.path.insert(0, "/home/olivier/app1-colmap")

import numpy as np
import torch
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from gaussian_ortho.gaussian_model import GaussianModel
from gaussian_ortho.rasterizer import (
    _render_gsplat_inference, RasterSettings, make_view_matrix
)
from gaussian_ortho.ortho_renderer import render_orthophoto, compute_ortho_extent
from gaussian_ortho.colmap_loader import apply_sim3_to_points, load_colmap_reconstruction
import gsplat

WORKSPACE = "/mnt/j/workspace/vol_banyuls"
PLY = os.path.join(WORKSPACE, "gaussian_checkpoints_test2/final.ply")
OUT = WORKSPACE
GSD = 0.02
DEVICE = "cuda"

def load_model():
    """Load model with Sim3 + filtering (same as production pipeline)."""
    model = GaussianModel(sh_degree=3, fagk_enabled=True)
    model.load_ply(PLY)
    model = model.to(DEVICE)
    
    # Apply Sim3
    with open(os.path.join(WORKSPACE, "alignment_transform.json")) as f:
        tf = json.load(f)
    model.apply_sim3(tf)
    
    # Filter outliers
    _, _, pc, td = load_colmap_reconstruction(
        os.path.join(WORKSPACE, "dense"),
        os.path.join(WORKSPACE, "alignment_transform.json"),
    )
    geo_pts = apply_sim3_to_points(pc.points, tf)
    pad_m = 5.0
    pc_min = geo_pts.min(axis=0)
    pc_max = geo_pts.max(axis=0)
    
    xyz = model.positions.detach()
    scales = model.scales.detach()
    max_scale = scales.max(dim=-1).values
    in_bounds = (
        (xyz[:, 0] >= pc_min[0] - pad_m) & (xyz[:, 0] <= pc_max[0] + pad_m) &
        (xyz[:, 1] >= pc_min[1] - pad_m) & (xyz[:, 1] <= pc_max[1] + pad_m) &
        (xyz[:, 2] >= pc_min[2] - pad_m) & (xyz[:, 2] <= pc_max[2] + pad_m)
    )
    scale_thresh = torch.quantile(max_scale, 0.995).item()
    reasonable_scale = max_scale <= scale_thresh
    visible = model.opacity.squeeze(-1).detach() > 0.05
    keep = in_bounds & reasonable_scale & visible
    model.filter_by_mask(keep)
    
    # Clip anisotropy
    with torch.no_grad():
        log_s = model._scaling.data
        min_l, _ = log_s.min(dim=-1, keepdim=True)
        max_l, _ = log_s.max(dim=-1, keepdim=True)
        log_r = max_l - min_l
        thr = np.log(10.0)
        needs = log_r > thr
        if needs.any():
            mean_l = log_s.mean(dim=-1, keepdim=True)
            clamped = mean_l + (log_s - mean_l).clamp(-thr/2, thr/2)
            model._scaling.data = torch.where(needs.expand_as(log_s), clamped, log_s)
    
    print(f"Loaded {model.num_gaussians} Gaussians (after filtering)")
    
    # Store extent for rendering
    model._diag_extent = (
        float(pc_min[0] - pad_m), float(pc_max[0] + pad_m),
        float(pc_min[1] - pad_m), float(pc_max[1] + pad_m),
        float(pc_min[2] - pad_m), float(pc_max[2] + pad_m),
    )
    return model

def render_ortho_alpha(model, gsd=GSD):
    """Render ortho and return separate alpha and RGB arrays."""
    ext = model._diag_extent
    x_min, x_max = ext[0], ext[1]
    y_min, y_max = ext[2], ext[3]
    z_min, z_max = ext[4], ext[5]
    
    W = int(np.ceil((x_max - x_min) / gsd))
    H = int(np.ceil((y_max - y_min) / gsd))
    
    print(f"Rendering {W}x{H} at gsd={gsd}")
    
    half_w = W * gsd / 2.0
    half_h = H * gsd / 2.0
    cx_world = (x_min + x_max) / 2.0
    cy_world = (y_min + y_max) / 2.0
    z_top = z_max + 10.0
    
    R_c2w = np.array([[1,0,0],[0,-1,0],[0,0,-1]], dtype=np.float32)
    T_world = np.array([cx_world, cy_world, z_top], dtype=np.float32)
    viewmat = make_view_matrix(R_c2w, T_world)
    
    fx = W / (2.0 * half_w)
    fy = H / (2.0 * half_h)
    
    # Render a center crop (2000x2000) for efficiency
    crop_w, crop_h = min(2000, W), min(2000, H)
    # offsets from full image
    px0 = (W - crop_w) // 2
    py0 = (H - crop_h) // 2
    
    # Tile camera for center crop
    tile_x_min = x_min + px0 * gsd
    tile_x_max = x_min + (px0 + crop_w) * gsd
    tile_y_max = y_max - py0 * gsd
    tile_y_min = y_max - (py0 + crop_h) * gsd
    
    tile_cx = (tile_x_min + tile_x_max) / 2.0
    tile_cy = (tile_y_min + tile_y_max) / 2.0
    tile_half_w = (tile_x_max - tile_x_min) / 2.0
    tile_half_h = (tile_y_max - tile_y_min) / 2.0
    
    tile_fx = crop_w / (2.0 * tile_half_w)
    tile_fy = crop_h / (2.0 * tile_half_h)
    
    T_tile = np.array([tile_cx, tile_cy, z_top], dtype=np.float32)
    viewmat_tile = make_view_matrix(R_c2w, T_tile)
    
    K = torch.tensor([[tile_fx,0,crop_w/2.0],[0,tile_fy,crop_h/2.0],[0,0,1]], 
                      dtype=torch.float32, device=DEVICE).unsqueeze(0)
    vm = viewmat_tile.to(DEVICE).unsqueeze(0)
    
    means = model.positions
    quats = model.rotations
    scales = model.scales
    opacities = model.opacity.squeeze(-1)
    colors = model.features
    
    with torch.no_grad():
        render_colors, render_alphas, info = gsplat.rasterization(
            means=means, quats=quats, scales=scales,
            opacities=opacities, colors=colors,
            viewmats=vm, Ks=K,
            width=crop_w, height=crop_h,
            near_plane=0.01, far_plane=1000.0,
            sh_degree=min(1, model.active_sh_degree),
            eps2d=0.3,
            render_mode="RGB+ED",
            rasterize_mode="antialiased",
            camera_model="ortho",
            absgrad=False,
        )
        
    alpha = render_alphas[0, :, :, 0].cpu().numpy()  # (H, W)
    rgb = render_colors[0, :, :, :3].cpu().numpy()     # (H, W, 3)
    depth = render_colors[0, :, :, 3].cpu().numpy()    # (H, W)
    
    print(f"Rendered {crop_w}x{crop_h}, alpha range [{alpha.min():.3f}, {alpha.max():.3f}]")
    print(f"Alpha mean={alpha.mean():.3f}, white pixel fraction={np.mean(alpha > 0.99):.3f}")
    
    return rgb, alpha, depth, crop_w, crop_h

def analyze_alpha_bands(alpha, rgb, out_dir):
    """Check if bands are in alpha, RGB, or both."""
    H, W = alpha.shape
    
    # Row-mean analysis
    alpha_row_mean = alpha.mean(axis=1)
    rgb_row_mean = rgb.mean(axis=2)  # (H, 3) — average across columns
    gray_row_mean = rgb.mean(axis=(1,2))  # Average gray value per row
    
    # Normalize RGB by alpha to separate coverage from color
    eps = 1e-6
    rgb_norm = rgb / (alpha[:,:,None] + eps)
    rgb_norm_row_mean = rgb_norm.mean(axis=2)  # (H, 3)
    gray_norm_row_mean = rgb_norm.mean(axis=(1,2))
    
    # Plot row means
    fig, axes = plt.subplots(4, 1, figsize=(16, 16))
    
    crop_h = min(500, H)
    rows = np.arange(crop_h)
    
    axes[0].plot(rows, alpha_row_mean[:crop_h], 'k-', linewidth=0.5)
    axes[0].set_title(f'Alpha row mean (first {crop_h} rows)')
    axes[0].set_ylabel('Alpha')
    axes[0].set_ylim(0, 1.1)
    
    axes[1].plot(rows, gray_row_mean[:crop_h], 'k-', linewidth=0.5)
    axes[1].set_title(f'Gray (raw) row mean')
    axes[1].set_ylabel('Intensity')
    
    axes[2].plot(rows, gray_norm_row_mean[:crop_h], 'k-', linewidth=0.5)
    axes[2].set_title(f'Gray (alpha-normalized) row mean')
    axes[2].set_ylabel('Intensity / Alpha')
    
    # FFT of alpha row mean
    alpha_centered = alpha_row_mean - alpha_row_mean.mean()
    fft_alpha = np.abs(np.fft.rfft(alpha_centered))
    freqs = np.fft.rfftfreq(H, d=1.0)  # in cycles/pixel
    periods = np.zeros_like(freqs)
    periods[1:] = 1.0 / freqs[1:]
    
    # Also FFT of gray row mean
    gray_centered = gray_row_mean - gray_row_mean.mean()
    fft_gray = np.abs(np.fft.rfft(gray_centered))
    
    gray_norm_centered = gray_norm_row_mean - gray_norm_row_mean.mean()
    fft_gray_norm = np.abs(np.fft.rfft(gray_norm_centered))
    
    mask = (periods > 5) & (periods < 200)
    axes[3].plot(periods[mask], fft_alpha[mask] / fft_alpha[mask].max(), 'b-', label='Alpha FFT', alpha=0.7)
    axes[3].plot(periods[mask], fft_gray[mask] / fft_gray[mask].max(), 'r-', label='Gray FFT', alpha=0.7)
    axes[3].plot(periods[mask], fft_gray_norm[mask] / fft_gray_norm[mask].max(), 'g-', label='Gray/Alpha FFT', alpha=0.7)
    axes[3].set_title('FFT of row means (periods 5-200 px)')
    axes[3].set_xlabel('Period (pixels)')
    axes[3].set_ylabel('Normalized magnitude')
    axes[3].legend()
    axes[3].axvline(x=25, color='gray', linestyle='--', alpha=0.5, label='25px')
    
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "band_diagnosis_row_analysis.png"), dpi=150)
    plt.close()
    print("Saved row analysis plot")
    
    # Save alpha channel as image
    alpha_img = (np.clip(alpha, 0, 1) * 255).astype(np.uint8)
    from PIL import Image
    Image.fromarray(alpha_img).save(os.path.join(out_dir, "band_diagnosis_alpha.png"))
    
    # Save alpha crop (center 500x500)
    ch, cw = H//2, W//2
    crop = alpha[ch-250:ch+250, cw-250:cw+250]
    # Enhance contrast for visualization
    crop_vis = ((crop - crop.min()) / (crop.max() - crop.min() + 1e-8) * 255).astype(np.uint8)
    Image.fromarray(crop_vis).save(os.path.join(out_dir, "band_diagnosis_alpha_crop_enhanced.png"))
    print(f"Alpha crop stats: min={crop.min():.4f} max={crop.max():.4f} std={crop.std():.4f}")

def analyze_gaussian_positions(model, out_dir, gsd=GSD):
    """Check for periodicity in Gaussian Y-coordinate distribution."""
    positions = model.positions.detach().cpu().numpy()
    opacities = model.opacity.detach().cpu().squeeze(-1).numpy()
    scales_raw = model.scales.detach().cpu().numpy()
    
    # Focus on the center of the scene (where we see bands)
    x_med, y_med = np.median(positions[:, 0]), np.median(positions[:, 1])
    x_range = 5.0  # ±5m strip
    mask = np.abs(positions[:, 0] - x_med) < x_range
    y_vals = positions[mask, 1]
    z_vals = positions[mask, 2]
    opa_vals = opacities[mask]
    
    print(f"Center strip: {mask.sum()} Gaussians within ±{x_range}m of x_med={x_med:.1f}")
    
    fig, axes = plt.subplots(3, 1, figsize=(16, 12))
    
    # Y-coordinate histogram (bin width = GSD = 0.02m)
    y_bins = np.arange(y_vals.min(), y_vals.max(), gsd)
    counts, edges = np.histogram(y_vals, bins=y_bins)
    centers = (edges[:-1] + edges[1:]) / 2
    
    axes[0].bar(centers[:500], counts[:500], width=gsd*0.9, color='steelblue')
    axes[0].set_title(f'Gaussian Y-coordinate histogram (bin={gsd}m, center strip ±{x_range}m)')
    axes[0].set_xlabel('Y (meters)')
    axes[0].set_ylabel('Count')
    
    # FFT of histogram
    counts_centered = counts.astype(float) - counts.mean()
    fft_counts = np.abs(np.fft.rfft(counts_centered))
    freqs = np.fft.rfftfreq(len(counts), d=gsd)  # cycles/meter
    periods_m = np.zeros_like(freqs)
    periods_m[1:] = 1.0 / freqs[1:]
    
    mask_fft = (periods_m > 0.1) & (periods_m < 5.0)
    axes[1].plot(periods_m[mask_fft], fft_counts[mask_fft], 'b-')
    axes[1].set_title('FFT of Y-histogram')
    axes[1].set_xlabel('Period (meters)')
    axes[1].set_ylabel('|FFT|')
    axes[1].axvline(x=0.5, color='red', linestyle='--', label='0.5m (expected band period)')
    axes[1].legend()
    
    # Y vs Z scatter (to see if there are layers)
    subsample = np.random.choice(len(y_vals), min(50000, len(y_vals)), replace=False)
    axes[2].scatter(y_vals[subsample], z_vals[subsample], s=0.1, alpha=0.1, c='steelblue')
    axes[2].set_title('Y vs Z positions (center strip)')
    axes[2].set_xlabel('Y (meters)')
    axes[2].set_ylabel('Z (meters)')
    axes[2].set_aspect('auto')
    
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "band_diagnosis_positions.png"), dpi=150)
    plt.close()
    print("Saved position analysis plot")

def render_perspective_comparison(model, out_dir):
    """Render from perspective (as if from a single training camera) and compare."""
    ext = model._diag_extent
    x_mid = (ext[0] + ext[1]) / 2.0
    y_mid = (ext[2] + ext[3]) / 2.0
    z_max = ext[5]
    
    # Simulate a nadir perspective camera at ~80m above scene
    height = 80.0
    cam_z = z_max + height
    
    # Perspective camera looking straight down
    R_c2w = np.array([[1,0,0],[0,-1,0],[0,0,-1]], dtype=np.float32)
    T_world = np.array([x_mid, y_mid, cam_z], dtype=np.float32)
    viewmat = make_view_matrix(R_c2w, T_world)
    
    # PINHOLE intrinsics similar to training (fx=1894/2=947 for data_factor=2)
    W, H = 1400, 934  # Half of 2800x1867
    fx = fy = 947.0
    
    K = torch.tensor([[fx,0,W/2.0],[0,fy,H/2.0],[0,0,1]], dtype=torch.float32, device=DEVICE).unsqueeze(0)
    vm = viewmat.to(DEVICE).unsqueeze(0)
    
    means = model.positions
    quats = model.rotations
    scales = model.scales
    opacities = model.opacity.squeeze(-1)
    colors = model.features
    
    with torch.no_grad():
        # Perspective render
        render_colors_p, render_alphas_p, _ = gsplat.rasterization(
            means=means, quats=quats, scales=scales,
            opacities=opacities, colors=colors,
            viewmats=vm, Ks=K,
            width=W, height=H,
            near_plane=0.01, far_plane=1000.0,
            sh_degree=min(1, model.active_sh_degree),
            eps2d=0.3,
            render_mode="RGB",
            rasterize_mode="antialiased",
            camera_model="pinhole",   # PERSPECTIVE
            absgrad=False,
        )
        
        # Ortho render of same area
        # GSD from perspective: at z_med, GSD ≈ height / fx
        gsd_equiv = height / fx  # ~0.084m
        half_w_m = W * gsd_equiv / 2.0
        half_h_m = H * gsd_equiv / 2.0
        
        fx_ortho = W / (2 * half_w_m)
        fy_ortho = H / (2 * half_h_m)
        
        K_ortho = torch.tensor([
            [fx_ortho, 0, W/2.0],
            [0, fy_ortho, H/2.0],
            [0, 0, 1]
        ], dtype=torch.float32, device=DEVICE).unsqueeze(0)
        
        # Same camera position for ortho
        render_colors_o, render_alphas_o, _ = gsplat.rasterization(
            means=means, quats=quats, scales=scales,
            opacities=opacities, colors=colors,
            viewmats=vm, Ks=K_ortho,
            width=W, height=H,
            near_plane=0.01, far_plane=1000.0,
            sh_degree=min(1, model.active_sh_degree),
            eps2d=0.3,
            render_mode="RGB",
            rasterize_mode="antialiased",
            camera_model="ortho",   # ORTHO
            absgrad=False,
        )
    
    bg = torch.tensor([1,1,1], dtype=torch.float32, device=DEVICE)
    
    img_p = render_colors_p[0, :, :, :3] + bg.view(1,1,3) * (1 - render_alphas_p[0])
    img_o = render_colors_o[0, :, :, :3] + bg.view(1,1,3) * (1 - render_alphas_o[0])
    
    img_p = (img_p.clamp(0,1).cpu().numpy() * 255).astype(np.uint8)
    img_o = (img_o.clamp(0,1).cpu().numpy() * 255).astype(np.uint8)
    
    from PIL import Image as PILImage
    PILImage.fromarray(img_p).save(os.path.join(out_dir, "band_diagnosis_perspective.png"))
    PILImage.fromarray(img_o).save(os.path.join(out_dir, "band_diagnosis_ortho_same_area.png"))
    
    # Row-mean comparison
    gray_p = img_p.mean(axis=2).astype(float)
    gray_o = img_o.mean(axis=2).astype(float)
    
    fig, axes = plt.subplots(2, 1, figsize=(16, 8))
    rows = np.arange(min(300, H))
    axes[0].plot(rows, gray_p[:len(rows)].mean(axis=1), 'b-', linewidth=0.5, label='Perspective')
    axes[0].plot(rows, gray_o[:len(rows)].mean(axis=1), 'r-', linewidth=0.5, label='Ortho')
    axes[0].set_title('Row-mean gray: Perspective vs Ortho')
    axes[0].legend()
    
    axes[1].plot(rows, gray_p[:len(rows)].mean(axis=1) - gray_o[:len(rows)].mean(axis=1), 'k-', linewidth=0.5)
    axes[1].set_title('Difference (Persp - Ortho) row mean')
    
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "band_diagnosis_persp_vs_ortho.png"), dpi=150)
    plt.close()
    print("Saved perspective comparison")

def main():
    model = load_model()
    
    print("\n=== 1. RENDER ORTHO WITH ALPHA ===")
    rgb, alpha, depth, W, H = render_ortho_alpha(model)
    
    print("\n=== 2. ANALYZE ALPHA AND RGB BANDS ===")
    analyze_alpha_bands(alpha, rgb, OUT)
    
    print("\n=== 3. ANALYZE GAUSSIAN POSITIONS ===")
    analyze_gaussian_positions(model, OUT)
    
    print("\n=== 4. PERSPECTIVE vs ORTHO COMPARISON ===")
    render_perspective_comparison(model, OUT)
    
    print("\n=== DONE ===")

if __name__ == "__main__":
    main()
