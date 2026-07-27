# Phase 4 MRNF DC-plus-opacity combination — dev.20

Date: 2026-07-25  
Version: `0.5.0-dev.20`  
Base revision during measurement: `87d67d2` plus the documented dev.20
worktree changes

## Question

Can LichtFeld's DC behavior, which improved PSNR but slightly reduced SSIM in
dev.19, be combined with the broadly beneficial opacity behavior without
changing DroneGS position, scale, or rotation?

## Profile contract

Dev.20 adds `lichtfeld-dc-opacity`:

| Family | Behavior |
|---|---|
| DC | LichtFeld LR `0.002`, epsilon `1e-15` |
| Opacity | LichtFeld LR `0.012`, epsilon `1e-15` |
| Position | dev16 full-bounding-box schedule, epsilon `1e-8` |
| Scale | dev16 constant `0.005`, epsilon `1e-8` |
| Rotation | dev16 `0.001`, epsilon `1e-8` |

The profile changes no objective, rasterizer, topology, edge guidance,
selection seed, camera schedule, dataset split, or geometry optimizer.

Step-one telemetry confirms exact isolation:

| Family | Control update RMS | Opacity-only | DC+opacity |
|---|---:|---:|---:|
| DC | 0.0152966 | 0.0152966 | 0.000751673 |
| Opacity | 0.00307959 | 0.00454996 | 0.00454996 |
| Position | 0.0029868 | 0.0029868 | 0.0029868 |

## Correctness gates

All five native executables pass:

- `dronegs_core_tests`
- `dronegs_rasterization_tests`
- `dronegs_cuda_tests`
- `dronegs_rasterization_cuda_tests`
- `dronegs_training_tests`

The tests verify CLI selection, DC and opacity rates/epsilons, and unchanged
position/scale/rotation behavior.

## Dataset protocol

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Fingerprint | `fnv1a64:b52de467fbfc898e` |
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Resolution | 800 × 580 |
| Seed | 42 |
| Initial Gaussians | 1,025,093 |
| Maximum Gaussians | 1,500,000 |
| Held-out rule | `scene_index % 8 == 0` |
| Final binary SHA-256 | `6ebbc4fede3e0a48e7c75851a1302845df7c8acc81ea0964b9efcdb2d2802767` |

## Preliminary 500-step gate

Output:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev20-gate-dc-opacity-500/
```

The combination reaches:

- PSNR: `17.20034218` dB;
- SSIM: `0.2457397729`;
- final objective: `0.2368587106`;
- final Gaussians: `1,173,573`;
- trainer compute: `58.9661` seconds;
- manifest wall: `78.5023` seconds.

Relative to the dev19/dev16 500-step quality anchor, this is approximately
`+0.1284 dB` and `+0.000239 SSIM`. The gate therefore justified a longer,
same-binary comparison.

## Same-binary 1,000-step protocol

Outputs:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev20-long-control-1000/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev20-long-opacity-1000/
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev20-long-dc-opacity-1000/
```

All three runs use the exact final dev.20 binary. At 1,000 steps, topology has
five refinement windows rather than the two present at 500 steps.

## Aggregate quality

| Profile | Final objective | PSNR | Δ PSNR | SSIM | Δ SSIM | Gaussians |
|---|---:|---:|---:|---:|---:|---:|
| dev16 control | 0.2285692 | 17.514029 | — | 0.251635 | — | 1,437,504 |
| Opacity-only | 0.2280344 | 17.521090 | +0.007061 | **0.251892** | **+0.000257** | 1,437,512 |
| DC+opacity | **0.2271346** | **17.631939** | **+0.117910** | 0.251785 | **+0.000150** | 1,437,501 |

The combination retains an aggregate SSIM improvement while providing a
substantially larger PSNR gain.

Against opacity-only, DC+opacity changes:

- PSNR: `+0.110849 dB`;
- SSIM: `-0.000107`.

## Per-view distribution

Against the dev16 control:

| Profile | PSNR wins / losses | PSNR median Δ | SSIM wins / losses | SSIM median Δ |
|---|---:|---:|---:|---:|
| Opacity-only | 109 / 63 | +0.006152 dB | **130 / 42** | **+0.000285** |
| DC+opacity | **142 / 30** | **+0.103690 dB** | 66 / 106 | -0.000379 |

DC+opacity's positive mean SSIM is driven by larger improvements on a minority
of views:

- SSIM minimum delta: `-0.003984`;
- SSIM maximum delta: `+0.008241`;
- mean delta: `+0.000150`;
- median delta: `-0.000379`.

Opacity-only is much more homogeneous:

- SSIM minimum delta: `-0.001107`;
- SSIM maximum delta: `+0.001369`;
- mean delta: `+0.000257`;
- median delta: `+0.000285`.

Against opacity-only directly, the combination wins PSNR on 137/172 views but
loses SSIM on 110/172 views.

## Topology

| Profile | Refinements | Added | Final population |
|---|---:|---:|---:|
| dev16 control | 5 | 412,411 | 1,437,504 |
| Opacity-only | 5 | 412,419 | 1,437,512 |
| DC+opacity | 5 | 412,408 | 1,437,501 |

Population differs by only eleven Gaussians. The quality differences arise
from optimizer trajectories, not population size.

## Final effective updates

At step 1, all three runs share identical incoming gradients. At step 1,000:

| Profile | DC update RMS | Opacity update RMS | Position update RMS |
|---|---:|---:|---:|
| dev16 control | 0.00741716 | 0.00182253 | 0.0000189450 |
| Opacity-only | 0.00740212 | 0.00266304 | 0.0000191322 |
| DC+opacity | 0.000472975 | 0.00277879 | 0.0000184676 |

The combination keeps position updates at the dev16 order of magnitude while
reducing DC and increasing opacity as intended.

## Performance

| Profile | Trainer seconds | Evaluation | Manifest wall | Process wall | Max RSS KiB |
|---|---:|---:|---:|---:|---:|
| dev16 control | 143.951 | 17.743 | 164.434 | 164.51 | 620,500 |
| Opacity-only | 153.314 | 19.660 | 175.402 | 175.48 | 620,260 |
| DC+opacity | 153.921 | 20.322 | 177.033 | 177.12 | 622,584 |

These are single sequential laptop runs. Decode, thermal, and projected-work
variance prevent accepting a speed conclusion. Neither candidate is promoted
on performance evidence.

## Artifacts

| Profile | PLY bytes | PLY SHA-256 |
|---|---:|---|
| dev16 control | 80,500,678 | `adb61f92ee452eb1e65935bf15980c110d25e9c87c86ae974864366cb6bf68a0` |
| Opacity-only | 80,501,126 | `b6cd01dbfaaf002f3301d0510b2849aff70193c1c166c76073c4934386c2a111` |
| DC+opacity | 80,500,510 | `2bac3840a777a135281fc60f6c9ff51cd5ff2b1877fb88b9fd988d661d75ac38` |

Each output also contains `trainer_run.json` and
`evaluation/metrics.csv`.

## GPL provenance

The DC/opacity rates and epsilon values originate from the pinned LichtFeld GPL
sources recorded in `docs/dronegs/GPL_COMPONENTS.md`. The strict combination
profile is a DroneAI addition inside the existing GPL-3.0-or-later combined
CUDA units. The linked native binary remains GPL-covered.

## Decision

- Keep `dronegs-dev16` as the default.
- Retain opacity-only as the most homogeneous quality candidate.
- Retain DC+opacity as the best-PSNR candidate that also improves aggregate
  SSIM.
- Do not promote DC+opacity as default because its median SSIM delta is
  negative and 106/172 SSIM views regress.
- Keep position, scale, and rotation on dev16 behavior.

The next optimizer slice should retain LichtFeld opacity and sweep intermediate
DC learning rates between `0.002` and `0.05`, for example `0.005`, `0.01`, and
`0.02`. The target is to keep most of the `+0.118 dB` PSNR gain while restoring
a positive median SSIM delta and a majority of SSIM view wins.

Do not tag `dronegs-v0.5.0`; quality and speed parity remain open.
