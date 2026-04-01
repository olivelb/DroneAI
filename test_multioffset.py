"""Quick test: render ortho with multiple Y-offsets and average to remove bands."""
import sys, os, json, time
import numpy as np
import torch
sys.path.insert(0, "/home/olivier/app1-colmap")

from gaussian_ortho.gaussian_model import GaussianModel
from gaussian_ortho.ortho_renderer import render_orthophoto
from gaussian_ortho.colmap_loader import apply_sim3_to_points, load_colmap_reconstruction
from PIL import Image

def main():
    device = torch.device("cuda")
    
    # Load model (use test2 checkpoint — known to have bands)
    model = GaussianModel(sh_degree=3, fagk_enabled=True)
    model.load_ply("/mnt/j/workspace/vol_banyuls/gaussian_checkpoints_test2/final.ply")
    model = model.to(device)
    
    with open("/mnt/j/workspace/vol_banyuls/alignment_transform.json") as f:
        tf = json.load(f)
    model.apply_sim3(tf)
    
    # Filter (same as production)
    _, _, pc, _ = load_colmap_reconstruction(
        "/mnt/j/workspace/vol_banyuls/dense",
        "/mnt/j/workspace/vol_banyuls/alignment_transform.json",
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
    
    print(f"Filtered to {model.num_gaussians} Gaussians")
    
    extent = (
        float(pc_min[0] - pad_m), float(pc_max[0] + pad_m),
        float(pc_min[1] - pad_m), float(pc_max[1] + pad_m),
        float(pc_min[2] - pad_m), float(pc_max[2] + pad_m),
    )
    
    gsd = 0.02
    band_period_m = 25 * gsd  # 0.5m
    n_samples = 5
    offsets = np.linspace(0, band_period_m, n_samples, endpoint=False)
    
    print(f"Multi-sample rendering: {n_samples} passes with Y-offsets {offsets}")
    
    accumulated_rgb = None
    accumulated_h = None
    
    for i, offset in enumerate(offsets):
        shifted_extent = (
            extent[0], extent[1],
            extent[2] + offset, extent[3] + offset,
            extent[4], extent[5],
        )
        t0 = time.time()
        result = render_orthophoto(model, gsd=gsd, extent=shifted_extent, device=device)
        dt = time.time() - t0
        print(f"  Pass {i+1}/{n_samples}: offset={offset:.3f}m, {dt:.1f}s")
        
        if accumulated_rgb is None:
            accumulated_rgb = result['rgb'].astype(np.float64)
            accumulated_h = result['height'].astype(np.float64)
        else:
            accumulated_rgb += result['rgb'].astype(np.float64)
            accumulated_h += result['height'].astype(np.float64)
        
        del result
        torch.cuda.empty_cache()
    
    final_rgb = (accumulated_rgb / n_samples).astype(np.uint8)
    final_h = (accumulated_h / n_samples).astype(np.float32)
    
    print(f"Final: {final_rgb.shape}")
    
    # Save center crop
    H, W = final_rgb.shape[:2]
    cy, cx = H // 2, W // 2
    crop = final_rgb[cy-400:cy+400, cx-400:cx+400]
    Image.fromarray(crop).save("/mnt/j/workspace/vol_banyuls/multioffset_5x_crop.png")
    
    # Also save single-pass for comparison
    result0 = render_orthophoto(model, gsd=gsd, extent=extent, device=device)
    crop0 = result0['rgb'][cy-400:cy+400, cx-400:cx+400]
    Image.fromarray(crop0).save("/mnt/j/workspace/vol_banyuls/single_pass_crop.png")
    
    # Row analysis
    from scipy import ndimage
    gray_multi = final_rgb[cy-500:cy+500, cx-500:cx+500].mean(axis=2).astype(float)
    gray_single = result0['rgb'][cy-500:cy+500, cx-500:cx+500].mean(axis=2).astype(float)
    
    row_std_multi = gray_multi.mean(axis=1).std()
    row_std_single = gray_single.mean(axis=1).std()
    sharp_multi = np.abs(ndimage.laplace(gray_multi)).mean()
    sharp_single = np.abs(ndimage.laplace(gray_single)).mean()
    
    print(f"\nSingle-pass: row_std={row_std_single:.2f}, sharpness={sharp_single:.2f}")
    print(f"Multi-5x:    row_std={row_std_multi:.2f}, sharpness={sharp_multi:.2f}")
    print(f"Row-std reduction: {(1 - row_std_multi/row_std_single)*100:.1f}%")

if __name__ == "__main__":
    main()
