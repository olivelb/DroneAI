# Phase 4 two-scene DC validation — dev.22

Date: 2026-07-26

Project version: `0.5.0-dev.22`

Documentation base revision: `988446c`

Benchmark binary version: `0.5.0-dev.21`

Benchmark binary SHA-256:
`96b9edc6b6d50f719c017fa6e051169268242d7c120b29d573790c1a7f146df4`

Benchmark manifests report Git revision:
`a69614a8733e392c23fd607149a065d20e51cfe4-dirty`

## Decision

Keep `dronegs-dev16` as the default throughput profile. Promote
`calibrated-dc-0.020-opacity` to the recommended quality profile, but do not
make it the global default before LPIPS and a larger-scene throughput gate.

The second-scene result is positive and reproducible. Across 306 held-out views
from Albagnac and Savères, DC=0.020 improves the same-binary controls by
+0.13101 dB and +0.001065 SSIM. It wins 303/306 PSNR comparisons and 264/306
SSIM comparisons.

DC=0.010 has the larger combined mean PSNR gain (+0.16354 dB), but its Savères
SSIM is effectively neutral and it wins only 204/306 combined SSIM views.
DC=0.020 is the more transferable quality setting.

Neither candidate is a speed improvement. DC=0.020 increases manifest wall
time by 8.2% on Albagnac and 19.3% on Savères. The project therefore exposes
an explicit throughput/quality choice instead of hiding the tradeoff.

## Savères preparation

Source acquisition:
`/mnt/y/PHOTOS_SAVERES/DJI_202409301129_001_saleres`

Prepared workspace:
`/home/olivier/droneAI-workspaces/saveres-mavic3e-full`

COLMAP preparation used 1,066 Mavic 3E RTK photographs, GPU SIFT at a 3,200 px
maximum dimension, spatial matching, incremental mapping, robust GPS
alignment, and image undistortion to 3,200 px.

| Metric | Result |
|---|---:|
| Selected source images | 1,066 |
| Registered and undistorted images | 1,065 (99.91%) |
| Sparse points3D | 642,161 |
| Mean reprojection error | 1.264917 px |
| Median reprojection error | 1.281332 px |
| Horizontal GPS median | 0.044703 m |
| Horizontal GPS p95 | 0.148720 m |
| Euclidean GPS median | 0.078007 m |
| Euclidean GPS p95 | 0.155702 m |
| Euclidean GPS maximum | 0.322889 m |

The Gaussian input is:
`/home/olivier/droneAI-workspaces/saveres-mavic3e-full/dense`

## Same-binary protocol

- Dataset fingerprint: `fnv1a64:65e7f5ec5e4d53f8`
- Images: 1,065 total, 931 training, 134 held out
- Held-out rule: `scene_index % 8 == 0`
- GPU: NVIDIA RTX 4070 Laptop, 8 GiB
- CUDA runtime: 12.8
- Strategy: MRNF, SH degree 0
- Maximum population: 1,500,000
- Input: resize factor 4, maximum width 1,600
- Tile mode: 4
- Seed: 42
- Loss: `0.8 * active-pixel L1 + 0.2 * (1 - SSIM)`
- Metric: full-frame PSNR and Gaussian 11x11 SSIM, sigma 1.5, valid padding
- Prefetch/decode: depth 1, one worker, full JPEG IDCT
- Final evaluation images were not persisted
- All six Savères comparisons use the exact same dev.21 binary

Command template:

```bash
/home/olivier/droneAI/app1-colmap/dronegs/build/dronegs \
  --data-path /home/olivier/droneAI-workspaces/saveres-mavic3e-full/dense \
  --output-path OUTPUT \
  --iter ITERATIONS \
  --strategy mrnf \
  --sh-degree 0 \
  --max-cap 1500000 \
  --resize-factor 4 \
  --max-width 1600 \
  --tile-mode 4 \
  --seed 42 \
  --run-manifest OUTPUT/trainer_run.json \
  --prefetch-depth 1 \
  --decode-workers 1 \
  --jpeg-idct-scale 0 \
  --test-every 8 \
  --save-eval-images 0 \
  --optimizer-profile PROFILE
```

## 500-step gate

| Profile | Final loss | PSNR (dB) | SSIM | Final Gaussians | Training (s) | Wall (s) | Process wall | Max RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.279140 | 16.084600 | **0.120677** | 735,195 | 41.241 | 61.565 | 61.63 s | 536,500 KiB |
| DC=0.010 + opacity | 0.278375 | **16.253723** | 0.119979 | 735,196 | 47.496 | 66.527 | 66.61 s | 534,448 KiB |
| DC=0.020 + opacity | **0.277813** | 16.239403 | 0.120640 | 735,196 | 40.603 | 67.427 | 67.49 s | 536,460 KiB |

Per-view deltas versus the same-binary control:

| Profile | Mean PSNR delta | PSNR wins | Mean SSIM delta | SSIM wins |
|---|---:|---:|---:|---:|
| DC=0.010 | +0.169123 dB | 124/134 | -0.000698 | 22/134 |
| DC=0.020 | +0.154803 dB | 129/134 | -0.000036 | 65/134 |

The short gate advances both candidates because the prior Albagnac result
showed positive SSIM at 1,000 steps. DC=0.020 is already substantially more
stable at the short budget.

## 1,000-step Savères confirmation

| Profile | Final loss | PSNR (dB) | SSIM | Final Gaussians | Training (s) | Wall (s) | Process wall | Max RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.264832 | 16.652428 | 0.131453 | 900,627 | 92.699 | 117.973 | 118.04 s | 544,136 KiB |
| DC=0.010 + opacity | 0.262960 | **16.838703** | 0.131405 | 900,628 | 111.908 | 135.159 | 135.23 s | 546,288 KiB |
| DC=0.020 + opacity | **0.262491** | 16.798561 | **0.132098** | 900,626 | 115.248 | 140.728 | 140.82 s | 542,660 KiB |

Per-view deltas versus the same-binary control:

| Profile | Mean PSNR delta | Median | p05 to p95 | PSNR wins | Mean SSIM delta | Median | p05 to p95 | SSIM wins |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DC=0.010 | +0.186276 dB | +0.159164 | +0.024269 to +0.457607 | 130/134 | -0.000048 | -0.000183 | -0.001509 to +0.001902 | 61/134 |
| DC=0.020 | +0.146133 dB | +0.121416 | +0.035410 to +0.329624 | 132/134 | +0.000644 | +0.000570 | -0.000510 to +0.001756 | 103/134 |

The final populations differ by at most two Gaussians. Quality differences
therefore come from the optimizer profile, not materially different topology.

## Two-scene consolidation

Albagnac contains 172 held-out views and Savères 134. The table combines all
306 per-view comparisons without scene reweighting.

| Profile | Mean PSNR delta | PSNR wins | Mean SSIM delta | SSIM wins |
|---|---:|---:|---:|---:|
| DC=0.010 | **+0.163544 dB** | 298/306 | +0.000726 | 204/306 |
| DC=0.020 | +0.131009 dB | **303/306** | **+0.001065** | **264/306** |

Per-scene 1,000-step aggregates:

| Scene | Profile | PSNR (dB) | SSIM | Training (s) | Manifest wall (s) |
|---|---|---:|---:|---:|---:|
| Albagnac | dev16 | 17.514513 | 0.251634 | 149.540 | 171.930 |
| Albagnac | DC=0.010 | 17.660347 | 0.252962 | 161.423 | 183.693 |
| Albagnac | DC=0.020 | 17.633739 | 0.253027 | 162.281 | 186.074 |
| Savères | dev16 | 16.652428 | 0.131453 | 92.699 | 117.973 |
| Savères | DC=0.010 | 16.838703 | 0.131405 | 111.908 | 135.159 |
| Savères | DC=0.020 | 16.798561 | 0.132098 | 115.248 | 140.728 |

## Performance interpretation

Relative to the same-scene dev16 control:

| Scene | Profile | Training delta | Manifest wall delta |
|---|---|---:|---:|
| Albagnac | DC=0.010 | +7.9% | +6.8% |
| Albagnac | DC=0.020 | +8.5% | +8.2% |
| Savères | DC=0.010 | +20.7% | +14.6% |
| Savères | DC=0.020 | +24.3% | +19.3% |

RSS and final population remain effectively unchanged. The extra time is
trainer compute rather than memory growth. The larger Savères slowdown means
that the current quality profile must remain opt-in for high-throughput work.

## Artifact hashes

Savères 1,000-step PLY SHA-256:

| Profile | SHA-256 |
|---|---|
| dev16 control | `bf3be1e835fd727e20d82eaedfe5e2c1029cb33751e4f530dc5827b4cd67ef6f` |
| DC=0.010 | `e32f9daeb02c7e286980bb483e506fddf9c586cc9209a2040fdd9678a912d20b` |
| DC=0.020 | `33d61a89ca85ba84117e9c9874e98373e5ba65c7c4505c19313e43b5396c8246` |

Savères 500-step PLY SHA-256:

| Profile | SHA-256 |
|---|---|
| dev16 control | `86d5cd5c497a8da1aa7ab6f703250ed43bea5f872b9da4129cdf7b746e3594dc` |
| DC=0.010 | `07cc70431284494b46eb5602988aa8056568fd439a6352f86505e02844d1bb6b` |
| DC=0.020 | `7e89a9b0ce9cc286d7fdcbd7e723411026a52cee8bf44374b5c27ceda8b256af` |

## GPL scope

Dev.22 does not change the dev.21 optimizer or CUDA implementation. The
executable still links `cuda/rasterization.cu` and `cuda/trainer.cu`, which are
GPL-3.0-or-later because dev.15-dev.21 adapted pinned LichtFeld behavior.
Version identifiers, original orchestration, manifests, tests, and this report
retain their existing MIT treatment where applicable. The linked binary
remains GPL-covered.

## Next gate

1. Add deterministic LPIPS evaluation for persisted held-out predictions.
2. Re-evaluate dev16 and DC=0.020 on Albagnac and Savères with LPIPS.
3. Run the throughput/default comparison on the combined approximately
   2,000-image Albagnac acquisition.
4. Continue parity work with progressive SH and missing MRNF
   prune/replacement/noise/decay behavior.
