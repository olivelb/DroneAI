# Phase 4 MRNF weighted Gumbel and edge guidance — dev.16

Date: 2026-07-25
Version: `0.5.0-dev.16`
Base revision during measurement: `bf8da73` plus the documented dev.16
worktree changes

## Scope

DroneGS dev.16 changes only MRNF parent selection relative to dev.15:

- candidates still require maximum normalized-SSIM-error-weighted alpha
  contribution above `0.003` and positive visibility;
- requested growth remains 7% every 200 steps through iteration 15,000;
- parent/child split geometry, opacity redistribution, capacity, optimizer
  reset, loss, schedules, training split, and evaluation remain unchanged;
- selection key becomes
  `log(refine_weight * edge_guidance) + Gumbel(0,1)`;
- edge guidance is `1 + 0.25 * edge_score / positive_median(edge_scores)`.

The CLI seed and refinement iteration deterministically define the selection
seed. SplitMix64 then generates one per-source-index open-interval uniform
variate. This removes LichtFeld's wall-clock seed and makes runs reproducible.

## Edge implementation choice

Pinned LichtFeld computes Canny maps and extra full rasterizations for at least
10 views or 8% of the dataset at each refinement. On 1,376 Albagnac images,
that would be about 110 additional image loads, Canny passes, and renders per
refinement.

DroneGS instead computes a luminance Sobel magnitude for each view already
used by training. Existing ordered-alpha backward accumulates
`transmittance_before * alpha * edge_magnitude` per Gaussian. Refinement
normalizes the positive accumulated scores by their median. Therefore dev.16
uses zero additional edge-render passes and remains suitable for datasets with
thousands of photos. It is behaviorally aligned but not a bit-for-bit port of
LichtFeld's Canny sampling.

## Correctness gates

All five direct native executables pass on the RTX 4070 Laptop:

- `dronegs_core_tests`
- `dronegs_rasterization_tests`
- `dronegs_cuda_tests`
- `dronegs_rasterization_cuda_tests`
- `dronegs_training_tests`

The topology test now creates three identical eight-parent contexts. Equal
seeds must produce bit-identical split geometry; a different seed must select
different parents. The existing forced one-to-two split continues to cover
capacity, geometry, copied attributes, opacity, optimizer reset, and statistic
reset.

The 200-step real-data smoke is preserved at:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev16-gumbel-edge-smoke-200-b/
```

It selected 1,024,895 candidates, added 71,743 Gaussians, and completed without
non-finite values.

## Albagnac protocol

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Fingerprint | `fnv1a64:b52de467fbfc898e` |
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Resolution | 800 × 580 |
| Iterations / seed | 500 / 42 |
| Initial Gaussians | 1,025,093 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev16-gumbel-edge-heldout-500-b/` |

## Growth result

| Iteration | Selection seed | Candidates | Added | Population |
|---:|---:|---:|---:|---:|
| 200 | 11,193,442,798,364,848,194 | 1,024,922 | 71,745 | 1,096,838 |
| 400 | 3,940,141,523,020,144,890 | 1,096,212 | 76,735 | 1,173,573 |

Dev.16 adds 148,480 Gaussians, three fewer than dev.15 and 33 more than the
pinned LichtFeld control. The population delta versus LichtFeld is 0.0028%.

## Held-out quality

| Metric | dev.14 | dev.15 | dev.16 | dev.16 vs dev.15 |
|---|---:|---:|---:|---:|
| PSNR (dB) | 17.115355 | 17.059732 | **17.071688** | **+0.011955** |
| SSIM | **0.246278** | 0.244958 | 0.245508 | **+0.000550** |

Against dev.15, dev.16 improves PSNR on 94 of 172 views and regresses on 78.
It improves SSIM on 119 views and regresses on 53. Against dev.14, it remains
lower by 0.043666 dB and 0.000771 SSIM.

The weighted stochastic choice plus edge guidance therefore reverses a small
part of dev.15's quality regression, but does not restore dev.14 or approach
the pinned LichtFeld result of 21.068552 dB / 0.631048 SSIM.

## Performance and artifacts

| Item | dev.15 | dev.16 | Change |
|---|---:|---:|---:|
| Trainer compute | 55.865 s | 60.683 s | +4.818 s / +8.6% |
| Evaluation | 17.757 s | 17.759 s | +0.002 s |
| Manifest wall | 75.417 s | 80.323 s | +4.907 s / +6.5% |
| Process wall | 75.50 s | 80.41 s | +4.91 s |
| Max RSS | 593,776 KiB | 601,260 KiB | +7,484 KiB |

The persistent edge arrays add roughly three float buffers at Gaussian/pixel
capacity. The dominant avoidable work is the per-step Sobel launch plus the
host copy/median/sort at refinement, not extra camera renders.

- PLY: 65,720,542 bytes
- PLY SHA-256:
  `a73f529992e02b6fc3a5cb866ebcc4ffff00fa5d4a5fbd2b9ac104098b9faaf7`
- Evaluation files: 173
- Evaluation directory: 239,452,485 bytes

## GPL provenance

Dev.16 was implemented after inspecting pinned LichtFeld GPL sources:

- `src/training/strategies/mrnf.cpp`
- `src/training/kernels/mrnf_kernels.cu`
- `src/training/rasterization/edge_rasterizer.cpp`
- `src/training/rasterization/edge_compute/rasterization/include/kernels_forward.cuh`

The exact revision and full prior dev.15 source list are recorded in
`docs/dronegs/GPL_COMPONENTS.md`. Local `cuda/rasterization.cu` and
`cuda/trainer.cu` remain explicitly GPL-3.0-or-later; the linked native binary
is GPL-covered.

## Decision

Accept dev.16 as the reproducible MRNF selection and scalable edge-guidance
slice. Do not tag `dronegs-v0.5.0`: the quality gap remains large, LPIPS is
still absent, and the 8.6% compute increase is not yet justified by the small
held-out gain.

The next controlled parity factor is LichtFeld's MRNF-specific learning-rate,
opacity, scale, rotation, and SH schedules. Separately profile a fused Sobel
path or refinement-window-only edge accumulation before keeping dev.16's edge
cost in a final backend.
