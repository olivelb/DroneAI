# Phase 4: post-KNN scale and rotation calibration

Date: 2026-07-26
Version: `0.5.0-dev.34`
Decision: retain as opt-in structure profile; keep dev.33 balanced-quality

## Change

Three isolated profiles combine the accepted dev.33 DC `0.010` and opacity
`0.096` behavior with LichtFeld's scale schedule (`0.007` to `0.005`), its
rotation rate (`0.002`), or both. Position, renderer, covariance, topology,
split, seed, and CUDA architecture policy remain unchanged.

All six CPU, CUDA, gradient, training, and LPIPS-tool suites pass. Tests
explicitly verify family isolation and Adam epsilon selection.

## Albagnac, 500 steps

| Profile | PSNR | SSIM | LPIPS | Train | Wall |
|---|---:|---:|---:|---:|---:|
| dev.33 | 18.011423 | 0.356863 | 0.688418 | 8.674 s | 49.016 s |
| scale | 18.227499 | 0.367352 | 0.686407 | 8.818 s | 49.397 s |
| rotation | 18.028301 | 0.358023 | not run | 8.739 s | 48.655 s |
| scale + rotation | **18.243999** | **0.368717** | **0.684152** | 9.404 s | 50.355 s |

The combination gains `0.232576 dB`, `0.011854 SSIM`, and `0.62% LPIPS`
against dev.33.

Geometry moves toward the LichtFeld distribution:

| Median | dev.33 | dev.34 combined | LichtFeld |
|---|---:|---:|---:|
| Geometric-mean scale | 0.004145 | 0.004354 | 0.003811 |
| Axis anisotropy | 1.082 | 1.125 | 1.451 |
| Rotation angle | 0.022 rad | 0.142 rad | 0.347 rad |

Rotation alone is nearly neutral because a nearly isotropic splat receives
little orientation signal. Scale anisotropy makes rotation useful.

## Independent scenes

| Scene | Profile | PSNR | SSIM | LPIPS |
|---|---|---:|---:|---:|
| GAJAN | dev.33 | 14.344964 | 0.281346 | 0.887209 |
| GAJAN | combined | **14.499367** | **0.285724** | **0.877339** |
| Savères | dev.33 | 17.678139 | 0.333977 | **0.738185** |
| Savères | scale | 17.750528 | 0.335845 | 0.743988 |
| Savères | combined | **17.762459** | **0.337647** | 0.740647 |

The combined profile improves PSNR and SSIM on all three scenes and LPIPS on
Albagnac and GAJAN. Savères LPIPS regresses by `0.33%`. Scale alone regresses
it by `0.79%`, showing that rotation mitigates but does not remove the
perceptual tradeoff.

## Decision

Keep `dev34-opacity096-lf-scale-rotation` as an opt-in structure/PSNR/SSIM
profile. Do not replace `calibrated-dc-0.010-opacity-0.096` as the balanced
quality recommendation because the provisional gate requires no LPIPS
regression. The next calibration should explore a stronger rotation update
after anisotropy develops, or a staged scale/rotation schedule, rather than
raising scale globally.

No COLMAP stage or source image was modified. This optimizer adaptation lives
in the already GPL-covered trainer/rasterizer translation units and introduces
no architecture-specific dispatch.
