#!/usr/bin/env python3
"""Quick render test: vary eps2d and chunk_size to isolate banding cause."""
import sys, os, json, time, math
import numpy as np
import torch
sys.path.insert(0, "/home/olivier/app1-colmap")

from gaussian_ortho.gaussian_model import GaussianModel
from gaussian_ortho.ortho_renderer import render_orthophoto, compute_ortho_extent
from gaussian_ortho.colmap_loader import apply_sim3_to_points, load_colmap_reconstruction
from gaussian_ortho.geo_writer import write_geotiff
from PIL import Image

def main():
    device = torch.device("cuda")
    
    # Load model
    model = GaussianModel(sh_degree=3, fagk_enabled=True)
    model.load_ply("/mnt/j/workspace/vol_banyuls/gaussian_checkpoints_test2/final.ply")
    model = model.to(device)
    
    # Apply Sim3
    with open("/mnt/j/workspace/vol_banyuls/alignment_transform.json") as f:
        tf = json.load(f)
    model.apply_sim3(tf)
    
    # Filter outliers (same as generate_gaussian_orthophoto)
    _, _, pc, td = load_colmap_reconstruction(
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
    print(f"Filtered to {model.num_gaussians} Gaussians")
    
    # Clip anisotropy with ratio 10
    max_aniso = 10.0
    with torch.no_grad():
        log_s = model._scaling.data
        min_l, _ = log_s.min(dim=-1, keepdim=True)
        max_l, _ = log_s.max(dim=-1, keepdim=True)
        log_r = max_l - min_l
        thr = np.log(max_aniso)
        needs = log_r > thr
        if needs.any():
            mean_l = log_s.mean(dim=-1, keepdim=True)
            clamped = mean_l + (log_s - mean_l).clamp(-thr/2, thr/2)
            model._scaling.data = torch.where(needs.expand_as(log_s), clamped, log_s)
    
    extent = (
        float(pc_min[0] - pad_m), float(pc_max[0] + pad_m),
        float(pc_min[1] - pad_m), float(pc_max[1] + pad_m),
        float(pc_min[2] - pad_m), float(pc_max[2] + pad_m),
    )
    
    gsd = 0.02
    
    # Test 1: Single tile (no chunking) — normal eps2d
    print("\n=== Test A: Single tile, no chunking (chunk_size=99999) ===")
    t0 = time.time()
    result = render_orthophoto(model, gsd=gsd, extent=extent, chunk_size=99999, device=device)
    print(f"  Rendered in {time.time()-t0:.1f}s: {result['rgb'].shape}")
    Image.fromarray(result['rgb']).save("/mnt/j/workspace/vol_banyuls/render_test_A_single.tif")
    # Save center crop
    cy, cx = result['rgb'].shape[0]//2, result['rgb'].shape[1]//2
    Image.fromarray(result['rgb'][cy-400:cy+400, cx-400:cx+400]).save(
        "/mnt/j/workspace/vol_banyuls/render_test_A_crop.png")
    
    del result; torch.cuda.empty_cache()
    
    # Test 2: Large eps2d to blur bands
    print("\n=== Test B: Large eps2d=10 ===")
    # Monkey-patch the rasterizer to use larger eps2d
    import gaussian_ortho.rasterizer as rast
    orig_render = rast._render_gsplat_inference
    
    def patched_render_eps10(model, settings, camera_model="ortho"):
        import gsplat
        device = model.positions.device
        W, H = settings.image_width, settings.image_height
        K = torch.tensor([[settings.fx, 0.0, settings.cx],
                          [0.0, settings.fy, settings.cy],
                          [0.0, 0.0, 1.0]], dtype=torch.float32, device=device).unsqueeze(0)
        viewmat = settings.viewmatrix.to(device).unsqueeze(0)
        bg_rgb = torch.tensor(list(settings.bg_color)[:3], dtype=torch.float32, device=device)
        means = model.positions
        quats = model.rotations
        scales = model.scales * settings.scaling_modifier
        opacities = model.opacity.squeeze(-1)
        colors = model.features
        sh_degree = min(1, model.active_sh_degree)
        
        with torch.no_grad():
            render_colors, render_alphas, _info = gsplat.rasterization(
                means=means, quats=quats, scales=scales, opacities=opacities,
                colors=colors, viewmats=viewmat, Ks=K,
                width=W, height=H,
                near_plane=settings.znear, far_plane=settings.zfar,
                sh_degree=sh_degree, eps2d=10.0,
                backgrounds=None, render_mode="RGB+ED",
                rasterize_mode="antialiased", camera_model=camera_model, absgrad=False,
            )
            alpha = render_alphas[0, :, :, 0]
            rgb = render_colors[0, :, :, :3].permute(2, 0, 1)
            rgb = rgb + (1.0 - alpha).unsqueeze(0) * bg_rgb.view(3, 1, 1)
            depth = render_colors[0, :, :, 3:4].permute(2, 0, 1)
        del render_colors, render_alphas, _info
        return {"image": rgb, "depth": depth}
    
    rast._render_gsplat_inference = patched_render_eps10
    t0 = time.time()
    result = render_orthophoto(model, gsd=gsd, extent=extent, chunk_size=99999, device=device)
    print(f"  Rendered in {time.time()-t0:.1f}s")
    cy, cx = result['rgb'].shape[0]//2, result['rgb'].shape[1]//2
    Image.fromarray(result['rgb'][cy-400:cy+400, cx-400:cx+400]).save(
        "/mnt/j/workspace/vol_banyuls/render_test_B_eps10_crop.png")
    del result; torch.cuda.empty_cache()
    
    # Test 3: Render at GSD=0.05 (coarser) to see if bands disappear
    print("\n=== Test C: GSD=0.05 (coarser, 2.5x) ===")
    rast._render_gsplat_inference = orig_render
    t0 = time.time()
    result = render_orthophoto(model, gsd=0.05, extent=extent, chunk_size=99999, device=device)
    print(f"  Rendered in {time.time()-t0:.1f}s: {result['rgb'].shape}")
    cy, cx = result['rgb'].shape[0]//2, result['rgb'].shape[1]//2
    Image.fromarray(result['rgb'][cy-400:cy+400, cx-400:cx+400]).save(
        "/mnt/j/workspace/vol_banyuls/render_test_C_gsd05_crop.png")
    del result; torch.cuda.empty_cache()
    
    print("\n=== All render tests done ===")

if __name__ == "__main__":
    main()
