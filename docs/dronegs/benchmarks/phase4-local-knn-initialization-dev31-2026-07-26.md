# Phase 4 local-KNN initialization gate

Date: 2026-07-26
Version: `0.5.0-dev.31`
Base commit: `688b1b8145fd331d98ade98ac303fa8e0248ee8a`

## Question

The pinned 500-step Albagnac controls have almost identical final populations
but very different quality:

- LichtFeld: 21.0686 dB / 0.6310 SSIM;
- DroneGS dev.30: 17.2290 dB / 0.2473 SSIM.

PLY inspection showed that dev.30 retained one nearly uniform scale
(`median=0.12876`), while LichtFeld had strongly local scales
(`median=0.00382`). Dev.31 tests whether MRNF's local initialization explains
part of the quality and convergence gap.

## Change

`src/model.cpp` now:

- computes robust central-75% scene extents;
- builds an original deterministic balanced 3D KD tree;
- performs parallel exact queries for the two nearest neighbours;
- initializes isotropic scale as
  `clamp((sqrt(d1²)+sqrt(d2²))/4, 1e-3, 0.1*robust_scene_size)`;
- retains LichtFeld's zero-log-scale fallback below three points.

The formula is adapted from pinned LichtFeld
`src/core/splat_data.cpp::compute_mrnf_knn_log_scales` at
`1004c0841a3776e3f67866ff34101fbc9677397f`. The translation unit is therefore
marked GPL-3.0-or-later and registered in `GPL_COMPONENTS.md`. The KD-tree
implementation itself is new DroneAI code and does not import nanoflann.

## Correctness

All six native suites pass:

- core;
- CPU rasterization;
- CUDA loss;
- CUDA rasterization and analytical gradients;
- CUDA training;
- LPIPS tooling.

New core cases cover compact versus diffuse neighbourhoods, isotropy,
duplicate points, finite output, and the small-cloud fallback.

## Controlled Albagnac comparison

Both runs use the existing read-only
`albagnac-mavic3e-full/dense` reconstruction:

| Parameter | Value |
|---|---|
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Initial points | 1,025,093 |
| Iterations | 500 |
| Maximum Gaussians | 1,500,000 |
| Resolution | 800 x 580 |
| SH degree | 0 |
| Seed | 42 |
| Optimizer | `calibrated-dc-0.020-opacity` |
| Held-out rule | `scene_index % 8 == 0` |

Outputs:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev30-quality-baseline-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev31-knn-500/
```

| Metric | dev.30 uniform | dev.31 local KNN | Difference |
|---|---:|---:|---:|
| Initial PSNR | 14.0631 | 8.0293 | -6.0338 dB |
| Initial SSIM | 0.18108 | 0.09672 | -0.08436 |
| Final PSNR | **17.2290** | 16.8093 | -0.4198 dB |
| Final SSIM | 0.24726 | **0.33732** | +0.09006 |
| Exact-pair LPIPS | 1.06330 | **0.78479** | -0.27851 (-26.2%) |
| Trainer compute | 73.61 s | **8.05 s** | -89.1% |
| Manifest wall | 101.32 s | **51.42 s** | -49.2% |
| Final Gaussians | 1,173,576 | 1,172,415 | -1,161 |

The KNN change is not a strict all-metric win: PSNR loses 0.42 dB. It is,
however, a large structural improvement in SSIM, perceptual distance, and
render/training work. The old large uniform splats produced artificially high
initial coverage but poor spatial detail.

## PLY diagnosis

| Distribution | dev.30 | dev.31 | LichtFeld 500 |
|---|---:|---:|---:|
| Median scale | 0.12876 | 0.00459 | 0.00382 |
| P99 scale | 0.12876 | 0.03843 | 0.04144 |
| Median opacity | 0.08592 | 0.15447 | 0.49107 |
| Color channels outside `[0,1]` | 8.09% | 22.01% | 3.72% |

Dev.31 now matches LichtFeld's scale regime closely. Its remaining opacity
gap is large, and 17.60% of all color channels finish above one. DroneGS
currently clamps SH color to `[0,1]` and suppresses the corresponding
gradient; those high channels are therefore frozen. LichtFeld permits
checkpoint/render color up to four.

## Decision and next gate

Retain local-KNN initialization as the new convergence baseline because it
substantially improves SSIM, LPIPS, and compute while exposing the next
renderer mismatch. Do not claim quality parity.

The next isolated gate is the LichtFeld-compatible SH color ceiling and
gradient interval. A later separate gate will evaluate projected covariance
dilation/no-maximum-clamp, because transplanting all renderer behavior at once
would make the source of any gain ambiguous.

No COLMAP stage, bundle adjustment, source photo, dense point cloud, or prior
benchmark artifact was modified.
