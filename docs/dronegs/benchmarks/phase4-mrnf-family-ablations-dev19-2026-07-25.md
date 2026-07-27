# Phase 4 MRNF one-family optimizer ablations — dev.19

Date: 2026-07-25  
Version: `0.5.0-dev.19`  
Base revision during measurement: `ee85fed` plus the documented dev.19
worktree changes

## Question

Which parameter family causes the dev.17/dev.18
`lichtfeld-absolute` regression, and does any pinned LichtFeld family improve
the accepted dev16 control when transferred alone?

## Exact isolation contract

Dev.19 retains:

- `dronegs-dev16`, the default and quality control;
- `lichtfeld-absolute`, the rejected full-profile control;
- `lichtfeld-dc-only`;
- `lichtfeld-opacity-only`;
- `lichtfeld-position-only`;
- `lichtfeld-scale-only`;
- `lichtfeld-rotation-only`.

Each `*-only` profile changes exactly one family's learning rate, schedule,
spatial normalization where applicable, and Adam epsilon. The other four
families retain all dev16 behavior.

Dev.18 used one global epsilon because both complete profiles use a uniform
epsilon. Dev.19 changes the CUDA Adam interface to five epsilons so a mixed
profile cannot leak `1e-15` into its control families.

| Profile | DC | Opacity | Position | Scale | Rotation |
|---|---|---|---|---|---|
| dev16 | dev16 | dev16 | dev16 | dev16 | dev16 |
| DC-only | LichtFeld | dev16 | dev16 | dev16 | dev16 |
| Opacity-only | dev16 | LichtFeld | dev16 | dev16 | dev16 |
| Position-only | dev16 | dev16 | LichtFeld | dev16 | dev16 |
| Scale-only | dev16 | dev16 | dev16 | LichtFeld | dev16 |
| Rotation-only | dev16 | dev16 | dev16 | dev16 | LichtFeld |

The sampled step-one telemetry confirms the contract. For example:

- DC-only reproduces LichtFeld's DC update RMS `0.000751673` while retaining
  dev16's position update RMS `0.0029868`;
- position-only retains dev16's DC update RMS `0.0152966` while reproducing
  LichtFeld's position update RMS `0.0000512862`.

## Correctness gates

All five direct executables pass on the RTX 4070 Laptop:

- `dronegs_core_tests`
- `dronegs_rasterization_tests`
- `dronegs_cuda_tests`
- `dronegs_rasterization_cuda_tests`
- `dronegs_training_tests`

The schedule fixture covers both complete profiles and all five family-only
profiles, including family epsilon and unchanged control-family rates.

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
| Test rule | `scene_index % 8 == 0` |

All six runs use the exact same dev.19 executable. Output directories are:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-final-control-dev16-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-final-ablation-dc-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-ablation-opacity-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-final-ablation-position-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-ablation-scale-500/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev19-ablation-rotation-500/
```

## Aggregate results

Changes are relative to the same-binary dev16 control.

| Profile | Final loss | PSNR | Δ PSNR | SSIM | Δ SSIM | Gaussians |
|---|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.2328672 | 17.071976 | — | 0.245501 | — | 1,173,572 |
| DC-only | 0.2390516 | **17.153784** | **+0.081808** | 0.245232 | -0.000269 | 1,173,574 |
| Opacity-only | **0.2323105** | 17.088200 | **+0.016224** | **0.245784** | **+0.000283** | 1,173,573 |
| Position-only | 0.2678436 | 15.944780 | **-1.127195** | 0.216094 | **-0.029407** | 1,173,578 |
| Scale-only | 0.2330139 | 17.073246 | +0.001270 | 0.245526 | +0.000025 | 1,173,573 |
| Rotation-only | 0.2328720 | 17.072662 | +0.000687 | 0.245501 | +0.000001 | 1,173,572 |

Final population differs by at most six Gaussians. Population count does not
explain the quality differences.

## Per-view results

| Profile | PSNR wins / losses | SSIM wins / losses |
|---|---:|---:|
| DC-only | 110 / 62 | 67 / 105 |
| Opacity-only | **137 / 35** | **142 / 30** |
| Position-only | 2 / 170 | **0 / 172** |
| Scale-only | 96 / 76 | 101 / 71 |
| Rotation-only | 97 / 75 | 87 / 85 |

The opacity gain is small in aggregate but broad across views. The position
regression is both large and universal for SSIM.

## Interpretation by family

### Position: reject

The LichtFeld position normalization and schedule reduce the initial applied
position delta about 58x and later deltas about 50x. Alone, this costs
1.1272 dB and 0.02941 SSIM, worse than the already rejected full profile.
Other LichtFeld families partially compensate for it; they do not make it
acceptable. DroneGS must keep the dev16 position normalization/schedule until
an independently calibrated intermediate is tested.

### DC: quality tradeoff

LichtFeld DC alone improves mean PSNR by 0.0818 dB and 110/172 views, but
reduces mean SSIM by 0.000269 and regresses 105/172 SSIM views. This remains
within the provisional SSIM non-regression allowance of 0.002, but it is not a
no-compromise replacement. It is a candidate for combination or intermediate
rate tuning, not the default.

### Opacity: first no-compromise candidate

LichtFeld opacity alone improves final objective, PSNR, and SSIM. It wins on
137/172 PSNR views and 142/172 SSIM views. This is the strongest candidate
from dev.19, although the aggregate margins still require confirmation at a
longer iteration budget and on another dataset.

### Scale and rotation: neutral

Their changes are near numerical/run variance at 500 steps. Neither is
justified as a new default from this result.

## Performance

| Profile | Trainer seconds | Manifest wall | Process wall | Max RSS KiB |
|---|---:|---:|---:|---:|
| dev16 control | 60.064 | 79.128 | 79.20 | 601,404 |
| DC-only | 66.610 | 87.043 | 87.13 | 601,324 |
| Opacity-only | 60.254 | 80.365 | 80.44 | 599,268 |
| Position-only | 71.359 | 92.703 | 92.78 | 601,404 |
| Scale-only | 64.569 | 85.701 | 85.78 | 601,632 |
| Rotation-only | 66.264 | 87.969 | 88.05 | 601,380 |

These are single sequential laptop runs. Decode/power/thermal variance is
visible, so no speed claim is accepted from this table. Position-only's slower
trajectory is consistent with dev.18, but a repeated timing protocol would be
required for a performance decision.

## Artifacts

| Profile | PLY bytes | PLY SHA-256 |
|---|---:|---|
| dev16 | 65,720,486 | `88d17bfa41ecce1f0bcb326c216b5cb8a3eee2ad8fef02d871afd74bac7dd3e9` |
| DC-only | 65,720,598 | `accf920b8f6cfec964d3187b7244040d04050d96423205962ae3aa35f51900d6` |
| Opacity-only | 65,720,542 | `3a7b8c6aec064bd065e39d740be98588074284f332d57b1f1fd15e12d69674f7` |
| Position-only | 65,720,822 | `bc6d5c4b939c2f9c049d270bca0fe657a0c4a5ce094a6bc0f3d51ce6289f53b4` |
| Scale-only | 65,720,542 | `719de9f1c4eee6abe1d0bc82de3749e8e6a4998ececa76a75210ff2e846c17fd` |
| Rotation-only | 65,720,486 | `649e6918fe975f3c4b3054cd9341599cc930a3d46a1f154928e3e9b697d63ff4` |

Each output also contains `trainer_run.json` and
`evaluation/metrics.csv`.

## GPL provenance

The rates, schedules, spatial normalization, and epsilon values originate from
the pinned LichtFeld GPL sources recorded in
`docs/dronegs/GPL_COMPONENTS.md`. Dev.19's per-family epsilon interface and
ablation profiles are DroneAI additions inside the already combined
GPL-3.0-or-later CUDA units. The linked native binary remains GPL-covered.

## Decision

- Keep `dronegs-dev16` as the default quality anchor.
- Reject `lichtfeld-position-only`.
- Retain DC-only as a PSNR/SSIM tradeoff candidate.
- Accept opacity-only as the first candidate that improves both held-out
  aggregates, pending longer-budget and second-dataset confirmation.
- Do not promote scale-only or rotation-only.
- Do not test a DC-plus-position profile.

The next optimizer slice should test DC plus opacity while keeping dev16
position, scale, and rotation. It should then compare opacity-only and the
combination at a longer budget before changing the default.

Do not tag `dronegs-v0.5.0`; quality and speed parity remain open.
