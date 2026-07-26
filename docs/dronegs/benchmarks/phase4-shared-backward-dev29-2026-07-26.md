# Phase 4 shared-memory backward validation

Date: 2026-07-26

Version: `0.5.0-dev.29`

## Decision

Accept cooperative shared-memory batching in ordered-alpha backward.

On the two large drone scenes, dev.29 is faster than both native dev.28 and
the dev.26 PTX-JIT reference while preserving held-out PSNR, SSIM, exact-pair
LPIPS, and final topology. This closes the bounded three-scene speed-parity
gate. It does not close convergence-length quality parity or downstream visual
and production gates.

## Implementation

The forward renderer already loaded a batch of 256 projected splats once per
tile block and shared it across 256 pixel threads. Backward previously read
the same global projected record and depth/source key independently for every
pixel during:

1. front-to-back transmittance recomputation;
2. back-to-front gradient accumulation.

Dev.29 gives backward a shared batch of 256 compact projected splats plus 256
source indices. Every thread cooperatively loads one item. Pixels consume the
first pass in increasing pair order and the second pass in decreasing pair
order, retaining the exact blend and gradient equations. Edge tiles keep all
threads alive across barriers while invalid pixels skip arithmetic.

The change is inside the GPL-3.0-or-later
`app1-colmap/dronegs/cuda/rasterization.cu` translation unit. No new
LichtFeld-derived code is introduced; the existing conservative GPL boundary
is recorded in `docs/dronegs/GPL_COMPONENTS.md`.

## Inputs and scope

Existing COLMAP dense outputs were mounted read-only. No photo, feature
database, sparse model, bundle adjustment, undistortion result, or source
point cloud was modified. No COLMAP rerun and no combined approximately
2,000-photo Albagnac run was performed.

| Scene | Images | Initial Gaussians | Iterations | Held-out |
|---|---:|---:|---:|---:|
| GAJAN smoke | 25 | 9,324 | 1,200 | 5 |
| Savères Mavic 3E RTK | 1,065 | 642,161 | 220 | 17 |
| Albagnac Mavic 3E RTK | 1,376 | 1,025,093 | 220 | 22 |

## Throughput

| Scene | Train dev.26 | Train dev.28 | Train dev.29 | dev.29 vs dev.26 | Wall dev.26 | Wall dev.28 | Wall dev.29 | dev.29 vs dev.26 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GAJAN | 14.125 s | 13.443 s | 13.729 s | -2.8% | 16.786 s | 14.430 s | 14.839 s | -11.6% |
| Savères | 53.125 s | 55.772 s | 39.503 s | -25.6% | 62.492 s | 61.561 s | 43.970 s | -29.6% |
| Albagnac | 75.456 s | 79.872 s | 50.439 s | -33.2% | 83.941 s | 87.522 s | 56.213 s | -33.0% |

The one-shot synthetic backward benchmark changed only from 114.627 to
114.287 ms because it does not exercise the persistent trainer's full
progressive-SH and refinement-statistics workload. Real training is the
retained decision signal.

## Quality and topology

| Scene | PSNR | SSIM | LPIPS Alex v0.1 | Final Gaussians |
|---|---:|---:|---:|---:|
| GAJAN | 14.152053 | 0.206928 | 1.045813 | 13,989 |
| Savères | 15.721014 | 0.110610 | 1.201649 | 687,110 |
| Albagnac | 16.869785 | 0.240971 | 1.085547 | 1,096,829 |

LPIPS uses exact final RGB8 PPM prediction/target pairs, official package
version 0.1, AlexNet, `[-1, 1]` normalization, and 5, 17, and 22 views.

Against dev.26, Savères changes by -0.000148 dB PSNR, approximately
-0.000003 SSIM, and -0.000021 LPIPS. Albagnac changes by +0.000010 dB PSNR,
approximately zero SSIM, and -0.000067 LPIPS. These are numerical noise.
Both large scenes reproduce dev.26/dev.28 final topology exactly.

GAJAN remains faster than dev.26 and improves LPIPS, but ends one Gaussian
below dev.26 and two below dev.28 because atomic floating-point accumulation
order can alter later weighted-Gumbel selection on this six-refinement smoke
run. Its held-out metrics remain stable or improved.

## Automated evidence

- Six of six core, CPU rasterization, CUDA loss, CUDA rasterization, CUDA
  training, and LPIPS-tool suites pass.
- Existing CPU/CUDA forward and backward comparisons and finite-difference
  gradient tests cover the shared implementation.
- Equal-depth stable ordering remains covered.
- The retained large-scene outputs complete without CUDA OOM.

## Remaining gates

1. Add checkpoint/resume before convergence-length controls.
2. Compare same-view, same-budget convergence against pinned LichtFeld.
3. Complete visual fly-through, orthomosaic, and downstream detection
   non-regression.
4. Profile and device-reside host-mediated topology compaction for long runs.
5. Validate VRAM and throughput on another Ada GPU and at least one non-Ada
   architecture before making the 64-register tuning portable by default.
