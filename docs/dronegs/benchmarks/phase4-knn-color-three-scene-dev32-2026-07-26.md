# Phase 4: local-KNN and extended-color validation on three scenes

Date: 2026-07-26
Version: `0.5.0-dev.32`
Candidate: portable CUDA, deterministic local-KNN initialization, live SH
color interval `[0,4]`, balanced `dc_lr=0.010`

## Purpose

This control checks that the quality and convergence gains observed on
Albagnac are not scene-specific. It reuses existing read-only COLMAP dense
outputs for GAJAN, Savères, and Albagnac. No feature extraction, matching,
mapping, bundle adjustment, or source-photo modification was performed.

The short GAJAN and Savères controls use the same iteration count, split, SH
schedule, seed, and capacity as their dev.29 references. The Savères
1,000-step control reproduces the historical dev.21 SH0 configuration and is
the convergence check for the apparent short-run PSNR regression.

## Results

### GAJAN, 1,200 steps, progressive SH3, test every 5

| Metric | dev.29 shared backward | dev.32 KNN/color | Delta |
|---|---:|---:|---:|
| PSNR | 14.152053 dB | 13.875117 dB | -0.276936 dB |
| SSIM | 0.206928 | 0.269092 | +0.062164 |
| LPIPS Alex | 1.045813 | 0.916241 | -12.39% |
| Final Gaussians | 13,989 | 13,993 | +4 |
| Training | 13.729 s | 6.472 s | -52.86% |
| Wall | 14.839 s | 7.566 s | -49.01% |

### Savères, 220 steps, progressive SH3, test every 64

| Metric | dev.29 shared backward | dev.32 KNN/color | Delta |
|---|---:|---:|---:|
| PSNR | 15.721014 dB | 13.634645 dB | -2.086369 dB |
| SSIM | 0.110610 | 0.224959 | +0.114349 |
| LPIPS Alex | 1.201649 | 0.891896 | -25.78% |
| Final Gaussians | 687,110 | 686,739 | -371 |
| Training | 39.503 s | 3.874 s | -90.19% |
| Wall | 43.970 s | 25.376 s | -42.29% |

This deliberately short control shows much better structural/perceptual
quality but insufficient PSNR convergence. The next control holds the scene
and split constant while extending training.

### Savères, 1,000 steps, SH0, test every 8

| Metric | dev.21 historical | dev.32 KNN/color | Delta |
|---|---:|---:|---:|
| PSNR | 16.838703 dB | 17.538475 dB | +0.699772 dB |
| SSIM | 0.131405 | 0.330330 | +0.198925 |
| LPIPS Alex | not recorded | 0.798290 | n/a |
| Final Gaussians | 900,628 | 900,127 | -501 |
| Training | 111.908 s | 17.322 s | -84.52% (6.46x) |
| Wall | 135.159 s | 94.444 s | -30.12% |

The long control reverses the short-run PSNR deficit. Local-KNN initialization
therefore changes the convergence curve rather than imposing a lower
converged PSNR on Savères. Wall time is now dominated by image service:
71.764 s data loading/image wait versus 17.322 s trainer compute.

### Albagnac, 500 steps, SH0, test every 8

| Metric | dev.30 portable | dev.32 KNN/color | Delta |
|---|---:|---:|---:|
| PSNR | 17.229034 dB | 17.032278 dB | -0.196756 dB |
| SSIM | 0.247258 | 0.340845 | +0.093587 |
| LPIPS Alex | 1.063296 | 0.772055 | -27.39% |
| Final Gaussians | 1,173,576 | 1,172,452 | -1,124 |
| Training | 73.610 s | 9.373 s | -87.27% |
| Wall | 101.320 s | 50.734 s | -49.93% |

## Interpretation

The accepted dev.32 changes generalize across the three available scenes:

- every scene gains strongly in SSIM and LPIPS;
- trainer compute falls by roughly 53% to 90%;
- Savères exceeds the historical PSNR once allowed 1,000 steps;
- the remaining short Albagnac/GAJAN PSNR deficit is a convergence and
  photometric-calibration target, not a reason to revert local geometry;
- large-scene wall time is increasingly limited by exact-target JPEG decode
  and cache misses rather than rasterization or optimization.

The next isolated quality gate is opacity convergence. On Albagnac, dev.32
ends near median opacity `0.154`, while the pinned LichtFeld output is near
`0.491`. Any change must remain generic across recent NVIDIA architectures
and pass all three scenes; it must not rely on a per-architecture policy.

## Artifacts

- `/home/olivier/droneAI-workspaces/gajan-dronegs-dev32-color-dc010-1200`
- `/home/olivier/droneAI-workspaces/saveres-dronegs-dev32-color-dc010-220`
- `/home/olivier/droneAI-workspaces/saveres-dronegs-dev32-color-dc010-1000`
- `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev32-color-dc010-500`
