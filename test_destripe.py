"""Test de-striping approaches on existing ortho render."""
import sys, os, json
import numpy as np
import torch
sys.path.insert(0, "/home/olivier/app1-colmap")

from gaussian_ortho.gaussian_model import GaussianModel
from gaussian_ortho.ortho_renderer import render_orthophoto
from gaussian_ortho.colmap_loader import apply_sim3_to_points, load_colmap_reconstruction
from PIL import Image
from scipy import ndimage
import time

WORKSPACE = "/mnt/j/workspace/vol_banyuls"

def load_model(device="cuda"):
    model = GaussianModel(sh_degree=3, fagk_enabled=True)
    model.load_ply(os.path.join(WORKSPACE, "gaussian_checkpoints_test2/final.ply"))
    model = model.to(device)
    
    with open(os.path.join(WORKSPACE, "alignment_transform.json")) as f:
        tf = json.load(f)
    model.apply_sim3(tf)
    
    _, _, pc, _ = load_colmap_reconstruction(
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
    keep = in_bounds & (max_scale <= scale_thresh) & (model.opacity.squeeze(-1).detach() > 0.05)
    model.filter_by_mask(keep)
    
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
    
    extent = (
        float(pc_min[0] - pad_m), float(pc_max[0] + pad_m),
        float(pc_min[1] - pad_m), float(pc_max[1] + pad_m),
        float(pc_min[2] - pad_m), float(pc_max[2] + pad_m),
    )
    return model, extent

def approach1_multioffset_aligned(model, extent):
    """Multi-offset ortho render with proper pixel alignment."""
    gsd = 0.02
    n_samples = 25  # 25 offsets within one 25px band period (1px spacing)
    offsets = np.arange(n_samples) * gsd  # 0, 0.02, 0.04, ..., 0.48m
    
    # Render baseline
    t0 = time.time()
    result0 = render_orthophoto(model, gsd=gsd, extent=extent, device="cuda")
    H, W = result0['rgb'].shape[:2]
    print(f"  Base render: {W}x{H} in {time.time()-t0:.1f}s")
    
    # We'll accumulate in a buffer that's (H + n_samples) tall to handle shifts
    accum = np.zeros((H, W, 3), dtype=np.float64)
    count = np.zeros((H, W), dtype=np.float64)
    
    # Add base render
    accum += result0['rgb'].astype(np.float64)
    count += 1.0
    
    for i in range(1, n_samples):
        shifted_extent = (
            extent[0], extent[1],
            extent[2] + offsets[i], extent[3] + offsets[i],
            extent[4], extent[5],
        )
        result = render_orthophoto(model, gsd=gsd, extent=shifted_extent, device="cuda")
        rgb = result['rgb'].astype(np.float64)
        
        # This render is shifted UP by i pixels geographically
        # (y_max + offset → row 0 is further north by offset/gsd = i pixels)
        # To align: shift this render DOWN by i rows
        shift = i
        if shift < H:
            accum[shift:, :] += rgb[:H-shift, :]
            count[shift:, :] += 1.0
        
        del result
        torch.cuda.empty_cache()
        
        if (i+1) % 5 == 0:
            print(f"  Pass {i+1}/{n_samples}")
    
    # Average
    avg = accum / np.maximum(count, 1)[:, :, np.newaxis]
    return avg.astype(np.uint8)

def approach2_fft_destripe(img):
    """FFT-based horizontal band removal (notch filter at 25px period)."""
    result = np.zeros_like(img, dtype=np.float64)
    
    for ch in range(3):
        channel = img[:, :, ch].astype(np.float64)
        
        # 1D FFT along columns (vertical direction) for each column
        fft = np.fft.rfft(channel, axis=0)
        freqs = np.fft.rfftfreq(channel.shape[0])
        
        # Identify the band frequency: period ≈ 25 pixels
        # Frequency = 1/25 = 0.04 cycles/pixel
        target_freq = 1.0 / 25.0
        
        # Create notch filter: suppress frequencies near target and harmonics
        notch = np.ones(len(freqs), dtype=np.float64)
        for harmonic in range(1, 6):  # Remove fundamental + 4 harmonics
            f = target_freq * harmonic
            # Gaussian notch with width that scales with harmonic
            sigma = 0.003  # narrow notch
            notch *= 1.0 - np.exp(-0.5 * ((freqs - f) / sigma) ** 2)
        
        # Apply notch filter to every column
        fft *= notch[:, np.newaxis]
        result[:, :, ch] = np.fft.irfft(fft, n=channel.shape[0], axis=0)
    
    return np.clip(result, 0, 255).astype(np.uint8)

def approach3_guided_filter(img, alpha_channel=None):
    """Separate low-freq (destriped) and high-freq (preserved) bands."""
    result = np.zeros_like(img, dtype=np.float64)
    
    for ch in range(3):
        channel = img[:, :, ch].astype(np.float64)
        
        # Low-pass: uniform filter along Y with kernel = 25 (one band period)
        low_pass = ndimage.uniform_filter1d(channel, size=25, axis=0)
        
        # High-pass: the detail we want to keep
        high_pass = channel - low_pass
        
        # New low-pass: 2D smooth version (preserves real gradients)
        low_pass_2d = ndimage.uniform_filter(channel, size=(25, 1))
        
        # Combine: smooth low-freq + original high-freq
        result[:, :, ch] = low_pass_2d + high_pass
    
    return np.clip(result, 0, 255).astype(np.uint8)

def analyze(img, label):
    """Quick band metrics."""
    H, W = img.shape[:2]
    cy, cx = H//2, W//2
    center = img[cy-500:cy+500, cx-500:cx+500].mean(axis=2).astype(float)
    
    row_means = center.mean(axis=1)
    row_std = row_means.std()
    sharp = np.abs(ndimage.laplace(center)).mean()
    
    row_centered = row_means - row_means.mean()
    fft = np.abs(np.fft.rfft(row_centered))
    freqs = np.fft.rfftfreq(len(row_centered))
    periods = np.zeros_like(freqs)
    periods[1:] = 1.0 / freqs[1:]
    mask = (periods > 10) & (periods < 50)
    peak_period = peak_mag = 0
    if mask.any():
        peak_idx = np.argmax(fft[mask])
        peak_period = periods[mask][peak_idx]
        peak_mag = fft[mask][peak_idx]
    
    print(f"  {label}: row_std={row_std:.2f}, sharp={sharp:.2f}, "
          f"FFT peak period={peak_period:.0f}px mag={peak_mag:.0f}")
    return row_std, sharp

def save_crop(img, name):
    H, W = img.shape[:2]
    cy, cx = H//2, W//2
    crop = img[cy-400:cy+400, cx-400:cx+400]
    Image.fromarray(crop).save(os.path.join(WORKSPACE, name))

def main():
    device = "cuda"
    model, extent = load_model(device)
    print(f"Loaded {model.num_gaussians} Gaussians")
    
    # Baseline render
    print("\n=== BASELINE ===")
    t0 = time.time()
    result = render_orthophoto(model, gsd=0.02, extent=extent, device=device)
    baseline = result['rgb']
    print(f"  Rendered in {time.time()-t0:.1f}s")
    analyze(baseline, "Baseline")
    save_crop(baseline, "destripe_baseline_crop.png")
    
    # Approach 1: Multi-offset aligned
    print("\n=== APPROACH 1: Multi-offset (25 passes) ===")
    t0 = time.time()
    multi = approach1_multioffset_aligned(model, extent)
    print(f"  Done in {time.time()-t0:.1f}s")
    analyze(multi, "Multi-25x")
    save_crop(multi, "destripe_multi25_crop.png")
    
    # Approach 2: FFT notch filter
    print("\n=== APPROACH 2: FFT notch filter ===")
    fft_result = approach2_fft_destripe(baseline)
    analyze(fft_result, "FFT notch")
    save_crop(fft_result, "destripe_fft_crop.png")
    
    # Approach 3: Guided filter (low/high freq separation)
    print("\n=== APPROACH 3: Frequency separation ===")
    guided = approach3_guided_filter(baseline)
    analyze(guided, "Freq sep")
    save_crop(guided, "destripe_guided_crop.png")
    
    print("\n=== DONE ===")

if __name__ == "__main__":
    main()
