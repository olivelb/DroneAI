# Phase 4: staged rotation calibration

Date: 2026-07-26
Version: `0.5.0-dev.35`
Decision: retain `0.008` as an opt-in perceptual structure profile; continue
with an AbsGrad densification experiment

## Change

Two architecture-independent profiles preserve dev.34's scale schedule and
dev.33's DC/opacity calibration. Rotation LR is `0.001` during the first 40%
of optimizer steps, then switches to `0.004` or `0.008`. This allows scale
anisotropy to develop before stronger orientation updates.

The run manifest records initial/final rotation LR, schedule, and switch
fraction. All six CPU, CUDA, gradient, training, and LPIPS-tool suites pass.

## Albagnac, 500 steps

| Profile | PSNR | SSIM | LPIPS | Train | Wall |
|---|---:|---:|---:|---:|---:|
| dev.33 balanced | 18.011423 | 0.356863 | 0.688418 | 8.674 s | 49.016 s |
| dev.34 scale + rotation | 18.243999 | 0.368717 | 0.684152 | 9.404 s | 50.355 s |
| dev.35 staged `0.004` | 18.269089 | 0.370206 | 0.681553 | not retained | not retained |
| dev.35 staged `0.008` | **18.286057** | **0.371625** | **0.679227** | 9.061 s | 50.999 s |

The `0.008` profile improves dev.34 by `0.0421 dB`, `0.00291` SSIM, and
`0.72%` LPIPS. It improves dev.33 by `0.2746 dB`, `0.01476` SSIM, and
`1.34%` LPIPS.

## Independent scenes

| Scene | Profile | PSNR | SSIM | LPIPS | Train | Wall |
|---|---|---:|---:|---:|---:|---:|
| GAJAN | dev.33 | 14.344964 | 0.281346 | 0.887209 | 6.137 s | 7.176 s |
| GAJAN | dev.34 | **14.499367** | **0.285724** | 0.877339 | not retained | not retained |
| GAJAN | dev.35 `0.008` | 14.390007 | 0.285297 | **0.875988** | 6.177 s | 7.164 s |
| Savères | dev.33 | 17.678139 | 0.333977 | **0.738185** | 17.002 s | 89.108 s |
| Savères | dev.34 | **17.762459** | 0.337647 | 0.740647 | not retained | not retained |
| Savères | dev.35 `0.008` | 17.759613 | **0.337739** | 0.739060 | 15.317 s | 88.535 s |

Dev.35 consistently improves LPIPS over dev.34. It remains slightly behind
dev.33 on Savères LPIPS (`+0.12%`) and trades `0.109 dB` of GAJAN PSNR for a
small LPIPS gain. It therefore does not replace dev.33 as the balanced
recommendation.

## Next gate

The remaining error is unlikely to be solved by a global geometry LR alone.
The next experiment will add a homodirectional projected-gradient statistic:
each pixel's absolute X/Y center-gradient contribution is accumulated before
view aggregation, following the mechanism described by AbsGS. The statistic
will augment rather than replace MRNF's error/edge score in the first
ablation, preserving deterministic topology selection and the existing
renderer.

No COLMAP stage or source image was modified. The optimizer changes remain in
the already GPL-covered CUDA trainer/rasterizer units and introduce no
architecture-specific dispatch or new runtime dependency.
