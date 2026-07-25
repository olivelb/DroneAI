# Phase 4 MRNF intermediate-DC calibration — dev.21

Date: 2026-07-25

Project version: `0.5.0-dev.21`

Base revision: `a69614a8733e392c23fd607149a065d20e51cfe4`

Benchmark binary SHA-256:
`96b9edc6b6d50f719c017fa6e051169268242d7c120b29d573790c1a7f146df4`

## Decision

Select `calibrated-dc-0.010-opacity` as the primary balanced-quality candidate.
Retain `calibrated-dc-0.020-opacity` as the per-view robustness candidate.
Keep `dronegs-dev16` as the default until both candidates are replicated on a
second scene and evaluated with LPIPS.

The 1,000-step confirmation is a positive result. DC=0.010 improves the
same-binary control by +0.14583 dB and +0.001328 SSIM; 168/172 PSNR views and
143/172 SSIM views improve. DC=0.020 has slightly lower mean PSNR but is more
uniform: 171/172 PSNR and 161/172 SSIM views improve.

## Question and isolation

Dev.20 showed that combining LichtFeld's DC endpoint (`0.002`) with its opacity
family improved mean PSNR, but regressed per-view SSIM on 106/172 views. Dev.21
tests whether intermediate DC rates retain the PSNR gain without that broad
SSIM tradeoff.

Only the following differ from the dev16 control:

| Profile | DC LR | DC epsilon | Opacity LR | Opacity epsilon |
|---|---:|---:|---:|---:|
| `dronegs-dev16` | 0.050 | 1e-8 | 0.010 | 1e-8 |
| `lichtfeld-opacity-only` | 0.050 | 1e-8 | 0.012 | 1e-15 |
| `lichtfeld-dc-opacity` | 0.002 | 1e-15 | 0.012 | 1e-15 |
| `calibrated-dc-0.005-opacity` | 0.005 | 1e-15 | 0.012 | 1e-15 |
| `calibrated-dc-0.010-opacity` | 0.010 | 1e-15 | 0.012 | 1e-15 |
| `calibrated-dc-0.020-opacity` | 0.020 | 1e-15 | 0.012 | 1e-15 |

Position, scale, rotation, topology, data order, loss, seed, held-out split,
image decode, and evaluation remain exactly the dev16 configuration.

## Protocol

- Dataset:
  `/home/olivier/droneAI-workspaces/albagnac-mavic3e-full/dense`
- Dataset fingerprint: `fnv1a64:b52de467fbfc898e`
- Images: 1,376 total, 1,204 training, 172 held out
- Held-out rule: `scene_index % 8 == 0`
- GPU: NVIDIA RTX 4070 Laptop, 8 GiB
- CUDA runtime: 12.8
- Strategy: MRNF, SH degree 0
- Maximum population: 1,500,000
- Input: resize factor 4, maximum width 1,600
- Seed: 42
- Loss: `0.8 * active-pixel L1 + 0.2 * (1 - SSIM)`
- Metric: full-frame PSNR and Gaussian 11x11 SSIM, sigma 1.5, valid padding
- Prefetch/decode: depth 1, one worker, full JPEG IDCT
- All comparisons within each budget use the same final dev.21 binary.

All five native CPU/CUDA test executables passed before benchmarking:
`dronegs_core_tests`, `dronegs_rasterization_tests`, `dronegs_cuda_tests`,
`dronegs_rasterization_cuda_tests`, and `dronegs_training_tests`.

## 500-step sweep

| Profile | Final loss | PSNR (dB) | SSIM | Final Gaussians | Training (s) | Wall (s) |
|---|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.232892 | 17.070932 | 0.245490 | 1,173,571 | 59.572 | 79.447 |
| opacity only | 0.232322 | 17.088537 | 0.245777 | 1,173,572 | 66.916 | 87.609 |
| DC=0.002 + opacity | 0.236851 | 17.200624 | 0.245741 | 1,173,574 | 68.718 | 90.734 |
| DC=0.005 + opacity | 0.234677 | 17.240786 | 0.246403 | 1,173,574 | 69.557 | 90.772 |
| DC=0.010 + opacity | 0.233105 | **17.252737** | 0.246922 | 1,173,575 | 70.257 | 94.290 |
| DC=0.020 + opacity | **0.231385** | 17.224482 | **0.247053** | 1,173,576 | 71.492 | 93.069 |

Per-view deltas versus the same-binary control:

| Profile | Mean PSNR Δ | Median PSNR Δ | PSNR wins | Mean SSIM Δ | Median SSIM Δ | SSIM wins |
|---|---:|---:|---:|---:|---:|---:|
| opacity only | +0.017604 | +0.013441 | 138/172 | +0.000287 | +0.000285 | 141/172 |
| DC=0.002 | +0.129693 | +0.123116 | 123/172 | +0.000251 | -0.000151 | 84/172 |
| DC=0.005 | +0.169854 | +0.140698 | 140/172 | +0.000913 | +0.000366 | 98/172 |
| DC=0.010 | **+0.181805** | +0.146822 | 148/172 | +0.001432 | +0.000867 | 121/172 |
| DC=0.020 | +0.153549 | +0.142823 | **160/172** | **+0.001563** | **+0.001236** | **154/172** |

The short sweep advances DC=0.010 for mean PSNR and DC=0.020 for SSIM and
per-view robustness.

## 1,000-step confirmation

| Profile | Final loss | PSNR (dB) | SSIM | Final Gaussians | Training (s) | Wall (s) | Process wall | Max RSS |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.228840 | 17.514513 | 0.251634 | 1,437,506 | 149.540 | 171.930 | 172.01 s | 622,468 KiB |
| DC=0.010 + opacity | **0.224336** | **17.660347** | 0.252962 | 1,437,513 | 161.423 | 183.693 | 183.78 s | 620,356 KiB |
| DC=0.020 + opacity | 0.225565 | 17.633739 | **0.253027** | 1,437,514 | 162.281 | 186.074 | 186.17 s | 620,476 KiB |

Per-view deltas versus the same-binary control:

| Profile | Mean PSNR Δ | Median PSNR Δ | PSNR wins/losses | PSNR range | Mean SSIM Δ | Median SSIM Δ | SSIM wins/losses | SSIM range |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| DC=0.010 | +0.145834 | +0.133610 | 168/4 | -0.226873 to +0.447325 | +0.001328 | +0.000990 | 143/29 | -0.001058 to +0.006855 |
| DC=0.020 | +0.119227 | +0.109695 | 171/1 | -0.051745 to +0.304672 | +0.001393 | +0.001100 | 161/11 | -0.000490 to +0.005529 |

Directly comparing the candidates, DC=0.010 wins PSNR on 127/172 views and
adds +0.02661 dB on average. DC=0.020 wins SSIM on 105/172 views and adds
0.0000654 mean SSIM. The tradeoff is therefore small and explicit rather than
a broad quality regression.

Final PLY SHA-256:

| Profile | SHA-256 |
|---|---|
| dev16 control | `95c6e4abfdafbd92b8eb24233374f82e964b50a3b2206a5be85ad8c766ea0160` |
| DC=0.010 | `f31ade069d07cc4cd13fd11966adea6c9c971f6e270e96f3bc826a723921c778` |
| DC=0.020 | `8cbf9d5edc1a76b47d405f9524a0ef1f80db09e6964ee0d2029f57be0a95a6eb` |

## Performance interpretation

The candidates cost approximately 11.9-14.1 seconds of process wall time
(6.8-8.2%) versus dev16 over this 1,000-step run. The manifest attributes most
of the increase to trainer compute; process RSS is effectively unchanged.
This is not accepted as a speed improvement. It is a measured quality/compute
tradeoff and still far from the LichtFeld quality parity gate.

The similar final populations confirm that the quality change comes from the
optimizer profile rather than materially different densification counts.

## GPL scope

Dev.21 changes optimizer-profile selection and rates inside
`app1-colmap/dronegs/cuda/rasterization.cu` and
`app1-colmap/dronegs/cuda/trainer.cu`. Those combined units are already
GPL-3.0-or-later because dev.15-dev.20 adapted pinned LichtFeld behavior. The
dev.21 additions and the linked native binary therefore remain GPL-covered.
Original CLI, manifest, headers, tests, and documentation retain their existing
MIT licensing where applicable. Exact upstream revision and local paths remain
recorded in `docs/dronegs/GPL_COMPONENTS.md`.

## Next gate

1. Replay dev16, DC=0.010, and DC=0.020 on a second representative scene.
2. Compute LPIPS for both scenes.
3. Promote a new default only if PSNR, SSIM, LPIPS, and per-view coverage all
   remain non-regressive.
4. Then continue parity work with progressive SH and the missing MRNF
   prune/replacement/noise/decay behavior.
