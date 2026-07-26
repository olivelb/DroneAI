# Phase 4: post-KNN opacity calibration

Date: 2026-07-26
Version: `0.5.0-dev.33`
Decision: select opacity LR `0.096` as the recommended quality profile

## Purpose

Local-KNN initialization reduced the typical Gaussian footprint by roughly
two orders of magnitude. With the previous `0.012` opacity learning rate,
Albagnac ended near median opacity `0.167`, substantially below the pinned
LichtFeld result near `0.491`. Both trainers initialize opacity at `0.1`, so
the mismatch is convergence speed rather than initialization.

This ablation changes only opacity Adam's learning rate. DC remains `0.010`,
opacity epsilon remains `1e-15`, and position, scale, rotation, renderer,
loss, topology, seed, split, and local-KNN initialization remain unchanged.
The tested rates are `0.024`, `0.048`, and `0.096`.

All source COLMAP reconstructions were mounted read-only. No extraction,
matching, mapping, bundle adjustment, or source-photo modification was run.

## Automated checks

All six suites pass in Release with automatic native CUDA architecture
detection:

- core CLI/model tests;
- CPU rasterization tests;
- CUDA loss tests;
- CPU/CUDA rasterization and gradient parity;
- MRNF training/profile-isolation tests;
- external LPIPS tool tests.

The profile tests verify that the candidates retain DC `0.010`, the existing
family-specific epsilons, and dev.32 position/scale/rotation behavior.

## Albagnac 500-step sweep

| Opacity LR | PSNR | SSIM | LPIPS Alex | Train | Wall | Final Gaussians |
|---:|---:|---:|---:|---:|---:|---:|
| 0.012 (dev.32) | 17.032278 | 0.340845 | 0.772055 | 9.373 s | 50.734 s | 1,172,452 |
| 0.024 | 17.649893 | 0.354955 | 0.736600 | 9.800 s | 48.327 s | 1,172,362 |
| 0.048 | 17.933828 | **0.360318** | 0.706820 | 9.848 s | 51.347 s | 1,172,026 |
| 0.096 | **18.011423** | 0.356863 | **0.688418** | **8.674 s** | 49.016 s | 1,169,110 |

Against dev.32, `0.096` gains:

- `+0.979145 dB` PSNR;
- `+0.016018` SSIM;
- `-10.83%` LPIPS;
- `-7.46%` trainer compute;
- `-3.39%` wall time.

The `0.048` profile is retained as the maximum-SSIM point. The `0.096`
profile is selected because it has the best PSNR and LPIPS while still
improving SSIM materially over dev.32.

Albagnac median opacity progresses from `0.167` at `0.012` to `0.222` at
`0.024`, `0.298` at `0.048`, and `0.400` at `0.096`. The selected profile is
much closer to LichtFeld's `0.491` regime without changing initialization or
projected covariance.

## Independent-scene confirmation

### GAJAN, 1,200 steps, progressive SH3

| Metric | dev.32, 0.012 | dev.33, 0.096 | Delta |
|---|---:|---:|---:|
| PSNR | 13.875117 | 14.344964 | +0.469847 dB |
| SSIM | 0.269092 | 0.281346 | +0.012254 |
| LPIPS Alex | 0.916241 | 0.887209 | -3.17% |
| Training | 6.472 s | 6.137 s | -5.17% |
| Wall | 7.566 s | 7.176 s | -5.15% |

### Savères, 1,000 steps, SH0

| Metric | dev.32, 0.012 | dev.33, 0.096 | Delta |
|---|---:|---:|---:|
| PSNR | 17.538475 | 17.678139 | +0.139664 dB |
| SSIM | 0.330330 | 0.333977 | +0.003647 |
| LPIPS Alex | 0.798290 | 0.738185 | -7.53% |
| Training | 17.322 s | 17.002 s | -1.85% |
| Wall | 94.444 s | 89.108 s | -5.65% |
| Final Gaussians | 900,127 | 878,482 | -21,645 |

Savères median opacity reaches `0.428`. The higher rate also makes transparent
outliers reach the existing pruning threshold sooner, reducing the final
population by 2.4% without a quality loss.

## Interpretation

The gain is generic optimizer calibration, not architecture tuning. It does
not branch on compute capability, kernel policy, block size, register count,
or CUDA architecture. Local builds still use automatic native detection and
portable builds still select the compiled cubin at runtime.

The improvement generalizes across a small public-style scene, a 1,000+ image
Mavic 3E RTK scene, and the independent Albagnac Mavic 3E RTK reconstruction.
`calibrated-dc-0.010-opacity-0.096` becomes the recommended DroneGS quality
profile. The historical profiles remain available for reproducibility and
the command-line default remains unchanged while Phase 4 is experimental.

## Artifacts

- `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev33-opacity024-500`
- `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev33-opacity048-500`
- `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev33-opacity096-500`
- `/home/olivier/droneAI-workspaces/gajan-dronegs-dev33-opacity048-1200`
- `/home/olivier/droneAI-workspaces/gajan-dronegs-dev33-opacity096-1200`
- `/home/olivier/droneAI-workspaces/saveres-dronegs-dev33-opacity096-1000`
