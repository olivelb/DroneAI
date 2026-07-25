# Phase 4 analytical DSSIM result

Date: 2026-07-25
Status: correctness gate passed; isolated quality gain small; parity failed

## Controlled change

DroneGS `0.5.0-dev.14` changes only the ordered trainer objective:

```text
loss = 0.8 * active_pixel_L1 + 0.2 * (1 - valid_mean_SSIM)
```

The dataset, RGB8 targets, black background, split, fixed topology, renderer,
Adam schedules, SH degree, seed, and 500-step camera schedule are unchanged
from dev.13. The 11x11 Gaussian SSIM uses sigma 1.5, `C1=0.01^2`,
`C2=0.03^2`, RGB channels, data range 1, and valid padding.

The separable CUDA forward stores five analytical derivative terms per valid
window center. One thread per input RGB sample gathers the at-most 121
overlapping centers, so image-space DSSIM backward requires no atomics.

## Correctness evidence

All five direct native executables pass on the RTX 4070 Laptop:

```text
dronegs_core_tests
dronegs_rasterization_tests
dronegs_cuda_tests
dronegs_rasterization_cuda_tests
dronegs_training_tests
```

The training suite independently computes the complete objective on CPU and
compares it with CUDA. It also checks the exact trainer image gradient at
eight samples using central finite differences with epsilon `1e-3`.

The implementation is original MIT DroneAI code. No LichtFeld loss source was
copied. LichtFeld remains a separately pinned GPL control, recorded in
`GPL_COMPONENTS.md`.

## Albagnac protocol

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Fingerprint | `fnv1a64:b52de467fbfc898e` |
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Held-out rule | `scene_index % 8 == 0` |
| Resolution | 800 x 580 |
| Initial/final Gaussians | 1,025,093 / 1,025,093 |
| Iterations / seed | 500 / 42 |
| Strategy / SH degree | MRNF contract / 0 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev14-dssim-heldout-500/` |

## Result

| Metric | Initial | dev.13 L1 final | dev.14 L1+DSSIM final | dev.14 vs dev.13 |
|---|---:|---:|---:|---:|
| Mean PSNR | 14.063148 dB | 17.121188 dB | 17.115355 dB | -0.005834 dB |
| Median PSNR | 14.136738 dB | 17.126403 dB | 17.082636 dB | -0.043767 dB |
| Mean SSIM | 0.181077 | 0.241900 | 0.246278 | +0.004378 |
| Median SSIM | 0.170659 | 0.217396 | 0.221484 | +0.004088 |
| Mean active coverage | 0.996239 | 0.999862 | 0.999872 | +0.000009 |

Compared with dev.13, dev.14 improves SSIM on 171 of 172 held-out views.
PSNR improves on 73 views and regresses on 99; the mean PSNR change is
effectively neutral. Compared with initialization, dev.14 improves SSIM on all
172 views and PSNR on 171.

| Timing / artifact | dev.13 | dev.14 | Change |
|---|---:|---:|---:|
| Trainer compute | 39.310 s | 41.052 s | +4.43% |
| Initial + final evaluation | 18.812 s | 17.289 s | -8.10% |
| Native manifest wall | 61.109 s | 60.489 s | -1.01% |
| Measured process wall | not recorded | 60.56 s | diagnostic |
| Maximum process RSS | not recorded | 531,800 KiB | diagnostic |
| Final prediction PPM files | 172 | 172 | equal |
| Prediction bytes | 239,426,580 | 239,426,580 | equal |

The final PLY SHA-256 is:

```text
ff6ef489477677a7c33dcc20a6eb0c1c7eb25f14bce3f4f98bbc8679bd13c256
```

## Pinned LichtFeld comparison

The control remains the dev.13 run using the identical split/settings and
runtime image digest
`sha256:71913f535a208879b9cd2e84f17895849c51de53e457149bd12c85c95e44568f`.

| Metric | DroneGS dev.14 | Pinned LichtFeld | DroneGS gap |
|---|---:|---:|---:|
| Held-out PSNR | 17.115355 dB | 21.068552 dB | -3.953197 dB |
| Held-out SSIM | 0.246278 | 0.631048 | -0.384770 |
| Final Gaussians | 1,025,093 | 1,173,540 | -148,447 |

## Decision

Accept dev.14 as the DSSIM correctness slice: the analytical gradient is
verified and it delivers the expected directional SSIM gain for a modest
compute cost. Do not tag `dronegs-v0.5.0`; DSSIM alone does not materially
close the held-out gap.

The next controlled parity factor should be MRNF topology growth. The pinned
control creates 148,447 additional Gaussians by step 500, whereas DroneGS is
still fixed topology. Progressive SH cannot explain this specific SH-degree-0
control and should be measured after the topology gate or with a nonzero-SH
protocol.
