# Phase 4 MRNF effective-update calibration — dev.18

Date: 2026-07-25  
Version: `0.5.0-dev.18`  
Base revision during measurement: `420e5ae` plus the documented dev.18
worktree changes

## Question

Can the dev.17 quality regression be explained by actual optimizer deltas
rather than incoming gradient scale, and can the dev.16 quality anchor be
reproduced in the exact same instrumented binary?

Dev.18 changes no rasterizer, objective, topology, selection, edge-guidance,
dataset, split, camera schedule, seed, or iteration count. It retains two
selectable optimizer profiles:

- `dronegs-dev16`: the accepted quality anchor and dev.18 default;
- `lichtfeld-absolute`: the exact pinned constants tested in dev.17.

## Instrumentation

At optimizer step 1, every fifth of the requested run, and the final step, the
CUDA Adam kernel samples approximately 4,096 Gaussians deterministically using
the Gaussian index and a population-derived stride. For each of DC, opacity,
position, scale, and rotation it reports:

- current incoming gradient RMS;
- actual applied parameter-delta RMS;
- resulting parameter RMS;
- number of scalar components sampled.

The applied delta is measured after the real update path, including log-scale
bounds and quaternion renormalization. A zero current gradient can coexist
with a non-zero update because Adam moments persist from previous views.
Telemetry is diagnostic only: no sampled value feeds training.

## Correctness gates

All five direct native executables pass on the RTX 4070 Laptop:

- `dronegs_core_tests`
- `dronegs_rasterization_tests`
- `dronegs_cuda_tests`
- `dronegs_rasterization_cuda_tests`
- `dronegs_training_tests`

Coverage includes default and explicit CLI profile selection, exact learning
rates and spatial normalization for both profiles, schedule endpoints,
telemetry cadence, finite values, non-zero sample counts, and a measured
applied update.

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
| Maximum Gaussians | 1,500,000 |
| dev16 output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev18-calibration-dev16-500/` |
| LichtFeld-absolute output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev18-calibration-lichtfeld-500/` |

Both runs use the exact same dev.18 executable and pass the profile explicitly.

## Schedule audit

| Profile | Position initial → final | DC | Opacity | Scale initial → final | Rotation | Epsilon |
|---|---:|---:|---:|---:|---:|---:|
| dev16 | 0.00832225 → 0.0000832225 | 0.05 | 0.01 | 0.005 → 0.005 | 0.001 | 1e-8 |
| LichtFeld absolute | 0.000135236 → 0.00000136487 | 0.002 | 0.012 | 0.007 → 0.00500337 | 0.002 | 1e-15 |

The position normalizers remain the dev.16 full bounding-box diagonal
(`52.014063`) and LichtFeld 10th-90th-percentile median axis width
(`6.761782`), respectively.

## Same-state step-one audit

Before the first update, both profiles have the same model, camera, target,
objective, and gradients.

| Family | Gradient RMS, both profiles | dev16 update RMS | LichtFeld update RMS | dev16 / LichtFeld |
|---|---:|---:|---:|---:|
| DC | 2.62340e-7 | 1.52966e-2 | 7.51673e-4 | **20.350x** |
| Opacity | 5.28443e-7 | 3.07959e-3 | 4.54996e-3 | **0.677x** |
| Position | 7.55272e-6 | 2.98680e-3 | 5.12862e-5 | **58.238x** |
| Scale | 0 | 0 | 0 | n/a |
| Rotation | 0 | 0 | 0 | n/a |

Adam epsilon explains part of the difference between nominal LR ratios and
actual first-step deltas. DC's nominal LR ratio is 25x but its applied ratio is
20.35x. Position's nominal initial ratio is 61.5x but its applied ratio is
58.24x.

Relative to parameter RMS at step one:

| Family | dev16 update / parameter | LichtFeld update / parameter |
|---|---:|---:|
| DC | 1.699e-2 | 8.353e-4 |
| Opacity | 1.402e-3 | 2.071e-3 |
| Position | 1.195e-3 | 2.053e-5 |

## Update-ratio evolution

Ratios below are `dev16 actual update RMS / LichtFeld-absolute actual update
RMS`. Values above one mean dev16 moves more.

| Step | DC | Opacity | Position | Scale | Rotation |
|---:|---:|---:|---:|---:|---:|
| 1 | 20.350 | 0.677 | 58.238 | n/a | n/a |
| 100 | 16.835 | 0.559 | 48.271 | 0.214 | 0.001 |
| 200 | 17.536 | 0.586 | 52.355 | 0.019 | ~0 |
| 300 | 16.081 | 0.543 | 52.477 | 0.194 | 0.109 |
| 400 | 14.690 | 0.546 | 51.041 | 0.273 | 0.142 |
| 500 | 14.599 | 0.581 | 51.607 | 0.301 | 0.123 |

After the shared first step, trajectories diverge, but incoming gradient RMS
ratios remain of the same order: roughly 0.86-1.14 for DC, 0.91-1.39 for
opacity, and 0.59-0.90 for position at sampled later steps. The dominant
difference is therefore the optimizer's applied delta, not an orders-of-
magnitude difference in incoming gradients.

By steps 300-500, LichtFeld-absolute applies:

- about 15-16x smaller DC updates;
- about 51-52x smaller position updates;
- about 1.7-1.8x larger opacity updates;
- about 3.3-5.2x larger scale updates;
- about 7.0-9.2x larger rotation updates.

No single global multiplier can correct those opposing family directions.

## Growth and quality

| Metric | dev16 profile | LichtFeld-absolute | Change |
|---|---:|---:|---:|
| Final Gaussians | 1,173,573 | 1,173,577 | +4 |
| Gaussians added | 148,480 | 148,484 | +4 |
| Final objective | 0.2329156 | 0.2668530 | +0.0339374 |
| Held-out PSNR | **17.070448 dB** | 16.115808 dB | **-0.954639 dB** |
| Held-out SSIM | **0.245493** | 0.219508 | **-0.025984** |

The instrumented dev16 replay differs from the original dev.16 anchor by only
`-0.001240 dB` and `-0.0000151 SSIM`, confirming that profile selection and
telemetry do not materially perturb the result.

Population remains effectively identical, so topology count does not explain
the quality gap.

## Performance

| Item | dev16 profile | LichtFeld-absolute | Change |
|---|---:|---:|---:|
| Trainer compute | 60.115 s | 68.887 s | +8.773 s / +14.6% |
| Evaluation | 18.452 s | 18.966 s | +0.513 s |
| Manifest wall | 80.778 s | 90.272 s | +9.494 s / +11.8% |
| Process wall | 80.86 s | 90.35 s | +9.49 s |
| Max RSS | 603,272 KiB | 601,260 KiB | -2,012 KiB |

The different parameter trajectories change projected footprints and useful
tile/splat work; the telemetry itself samples only about 4,096 rows at six
steps and is not the plausible source of a 9.5-second difference.

## Artifacts

| Profile | PLY bytes | PLY SHA-256 |
|---|---:|---|
| dev16 | 65,720,542 | `a8cf38813d5628dccf6e6ab21923ed10b07dd96553516f6eeb6234a45b927084` |
| LichtFeld absolute | 65,720,766 | `eac3415c9b4fbde6a49f9eaf6691e25e2ad30d80a160df2433c3e2baf12da3f4` |

Each output also contains `trainer_run.json` and
`evaluation/metrics.csv`.

## GPL provenance

The dev.17 optimizer behavior was adapted after inspection of the pinned
LichtFeld GPL sources listed in `docs/dronegs/GPL_COMPONENTS.md`. Dev.18 keeps
that behavior as a selectable profile and instruments the combined optimizer
path. Local `cuda/rasterization.cu` and `cuda/trainer.cu` remain
GPL-3.0-or-later; the linked native binary is GPL-covered.

## Decision

Accept dev.18 instrumentation and dual-profile selection. Restore
`dronegs-dev16` as the default because it is the reproduced quality anchor.
Retain `lichtfeld-absolute` only as an explicit calibration experiment.

Do not invent a blended optimizer profile from aggregate ratios. Dev.19 should
start from dev16 and change one family at a time:

1. DC-only ablation;
2. position-only ablation;
3. DC plus position only if both individual changes are understood;
4. opacity-only ablation;
5. scale-only ablation;
6. rotation-only ablation.

Every ablation should use the same 500-step split, emit the same telemetry,
and be accepted only if held-out quality supports it. Instrumenting the actual
LichtFeld runtime later would provide a stronger target for equivalent-update
matching.

Do not tag `dronegs-v0.5.0`; quality and speed parity remain open.
