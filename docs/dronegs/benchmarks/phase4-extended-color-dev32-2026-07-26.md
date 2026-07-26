# Phase 4 extended SH color and KNN recalibration

Date: 2026-07-26
Version: `0.5.0-dev.32`
Base version: `0.5.0-dev.31`

## Cause under test

After dev.31 local-KNN initialization, 17.60% of final Albagnac color channels
were above one. DroneGS clamped those channels to one and suppressed their
DC/SH gradient. The pinned LichtFeld FastGS path instead clamps splat color to
`[0,4]` and keeps gradients live inside that interval.

This is especially relevant after KNN: smaller, locally sized splats drive
larger per-splat color corrections while opacity and coverage converge.

## Change

The CPU oracle and CUDA renderer/backward path now:

- clamp SH-derived splat color to `[0,4]`;
- preserve DC and active higher-order SH gradients over the same interval;
- leave final RGB display serialization independently bounded.

The behavior follows pinned LichtFeld
`rasterization_config.h`, `kernels_forward.cuh`, and `kernels_backward.cuh` at
`1004c0841a3776e3f67866ff34101fbc9677397f`. Both affected translation units
and the provenance register identify the GPL-3.0-or-later adaptation.

All six core, CPU rasterization, CUDA loss, CUDA rasterization/gradient, CUDA
training, and LPIPS-tool suites pass. New tests exercise color 1.5 with a live
gradient and color 5.0 clamped to four with a stopped gradient.

## Albagnac controls

All runs reuse the same read-only 1,376-image Albagnac COLMAP reconstruction,
1,025,093 initial points, 172 held-out images, seed 42, SH degree zero, 500
iterations, and 1.5 million capacity.

| Version/profile | PSNR | SSIM | LPIPS | Train | Wall | Gaussians |
|---|---:|---:|---:|---:|---:|---:|
| dev.30 uniform, DC=.020 | **17.2290** | 0.24726 | 1.06330 | 73.61 s | 101.32 s | 1,173,576 |
| dev.31 KNN `[0,1]`, DC=.020 | 16.8093 | 0.33732 | 0.78479 | **8.05 s** | 51.42 s | 1,172,415 |
| dev.32 KNN `[0,4]`, DC=.020 | 16.7871 | 0.33722 | **0.76684** | **8.01 s** | **48.95 s** | 1,172,415 |
| dev.32 KNN `[0,4]`, DC=.010 | 17.0323 | **0.34085** | 0.77206 | 9.37 s | 50.73 s | 1,172,452 |
| dev.32 KNN `[0,4]`, DC=.005 | 17.1017 | 0.33594 | 0.78304 | 10.40 s | 52.95 s | 1,172,459 |

Outputs:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev32-color-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev32-color-dc010-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev32-color-dc005-500/
```

At the identical DC=.020 rate, extended color is PSNR/SSIM-neutral within
small run noise and improves LPIPS by 2.29%. It does not change topology or
trainer compute.

The old DC=.020 recommendation was calibrated before local KNN. With KNN,
DC=.010 is the balanced profile: versus dev.31 it gains 0.2230 dB, 0.00353
SSIM, and 1.62% LPIPS, while preserving the approximately nine-fold training
compute reduction. DC=.005 buys another 0.0695 dB but gives back SSIM and
LPIPS, so it is only the PSNR-oriented point on the Pareto curve.

Compared with the dev.30 uniform-scale control, the balanced dev.32 result is
still 0.1968 dB lower, but improves SSIM by 0.09359, LPIPS by 27.4%, trainer
compute by 87.3%, and wall time by 49.9%.

## Decision

Retain `[0,4]` color and use `calibrated-dc-0.010-opacity` as the balanced KNN
quality profile. Quality parity with LichtFeld remains open: the pinned
LichtFeld control is 21.0686 dB / 0.6310 SSIM.

The next isolated renderer gate is projected covariance. DroneGS currently
spectrally clamps variance to `[0.5625,64]`; LichtFeld adds `0.3` to the
diagonal and has no maximum variance clamp. That difference can affect
coverage, scale gradients, and convergence independently of SH color.

No COLMAP stage, source photo, or existing point cloud was modified.
