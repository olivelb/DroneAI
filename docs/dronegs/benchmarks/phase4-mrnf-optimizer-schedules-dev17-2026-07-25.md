# Phase 4 MRNF optimizer schedules — dev.17

Date: 2026-07-25
Version: `0.5.0-dev.17`
Base revision during measurement: `63016da` plus the documented dev.17
worktree changes

## Question

Does copying LichtFeld's MRNF optimizer constants, spatial normalization,
epsilon, and exponential schedules close the quality gap while retaining the
dev.16 loss, rasterizer, topology, Gumbel selection, and edge guidance?

Dev.17 changes no other training factor.

## Pinned MRNF profile

| Parameter | dev.16 | dev.17 / pinned MRNF |
|---|---:|---:|
| DC LR | 0.05 | 0.002 |
| Opacity LR | 0.01 | 0.012 |
| Scale LR | 0.005 constant | 0.007 -> 0.005 exponential |
| Rotation LR | 0.001 | 0.002 |
| Position factor | 1.6e-4 -> 1.6e-6 | 2e-5 -> 2e-7 exponential |
| Position scale | full initial bounding-box diagonal | median initial 10th-90th-percentile axis width |
| Adam epsilon | 1e-8 | 1e-15 |

The exponential schedule uses `(optimizer_step - 1) / iterations`, matching
the effective pinned MRNF update order: step one uses the initial rate and the
last requested step remains one gamma above the nominal endpoint.

The context exposes the current rates read-only. Training emits initial and
final JSON schedule events, and manifest v1 records every constant and
normalization rule.

## Spatial scale audit

The initial Albagnac COLMAP cloud has 1,025,093 points.

| Measurement | Value |
|---|---:|
| 80% X width | 7.283512 |
| 80% Y width | 6.761782 |
| 80% Z width | 0.744031 |
| MRNF median width | 6.761782 |
| Full bounding-box diagonal | 52.014063 |
| dev.16 initial position LR | 0.00832225 |
| dev.17 initial position LR | 0.000135236 |

The schedule port therefore reduces Albagnac's initial position LR by 61.5x.
The DC LR simultaneously falls 25x.

## Correctness gates

All five direct native executables pass on the RTX 4070 Laptop:

- `dronegs_core_tests`
- `dronegs_rasterization_tests`
- `dronegs_cuda_tests`
- `dronegs_rasterization_cuda_tests`
- `dronegs_training_tests`

The new schedule fixture verifies:

- exact 10th/90th percentile indices and median-axis width;
- all five initial learning rates;
- Adam epsilon `1e-15`;
- unchanged rates at the first optimizer step;
- geometric-mean position and scale rates halfway through a two-step schedule.

The 200-step real-data smoke is preserved at:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev17-mrnf-schedule-smoke-200/
```

It completed without non-finite values but immediately exposed slower
convergence: 15.0070 dB / 0.198897 SSIM, versus 16.3466 dB / 0.229788 for the
dev.16 smoke.

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
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev17-mrnf-schedule-heldout-500/` |

Observed schedule endpoints:

| Stage | Position | DC | Opacity | Scale | Rotation | Epsilon |
|---|---:|---:|---:|---:|---:|---:|
| Initial | 0.000135236 | 0.002 | 0.012 | 0.007 | 0.002 | 1e-15 |
| Step 500 | 0.00000136487 | 0.002 | 0.012 | 0.00500337 | 0.002 | 1e-15 |

## Growth

| Iteration | Candidates | Added | Population |
|---:|---:|---:|---:|
| 200 | 1,024,769 | 71,734 | 1,096,827 |
| 400 | 1,096,430 | 76,750 | 1,173,577 |

Population differs from dev.16 by only four Gaussians and from pinned
LichtFeld by 37. Growth parity is therefore preserved.

## Held-out quality

| Metric | dev.16 | dev.17 | Change |
|---|---:|---:|---:|
| PSNR | 17.071688 dB | 16.115065 dB | **-0.956623 dB** |
| SSIM | 0.245508 | 0.219473 | **-0.026034** |

Against dev.16:

- PSNR improves on 3 of 172 views and regresses on 169;
- SSIM improves on 0 views and regresses on all 172.

The final objective also worsens from 0.232874 to 0.266866. The schedules do
not catch up by iteration 500.

## Performance and artifacts

| Item | dev.16 | dev.17 | Change |
|---|---:|---:|---:|
| Trainer compute | 60.683 s | 67.256 s | +6.573 s / +10.8% |
| Evaluation | 17.759 s | 18.247 s | +0.488 s |
| Manifest wall | 80.323 s | 87.298 s | +6.974 s / +8.7% |
| Process wall | 80.41 s | 87.39 s | +6.98 s |
| Max RSS | 601,260 KiB | 599,204 KiB | -2,056 KiB |

The slower updates leave larger or more overlapping splats active for longer;
the benchmark therefore evaluates more useful tile/splat pairs even though
the final population is unchanged.

- PLY: 65,720,766 bytes
- PLY SHA-256:
  `ec2edf4db90c8aa0a57e3bac06debcdde10c0cbe6aaccdbc7094089c616ec640`
- Evaluation files: 173
- Evaluation directory: 239,452,759 bytes

## GPL provenance

Dev.17 was implemented after inspecting pinned LichtFeld GPL sources:

- `src/training/strategies/mrnf.cpp`
- `src/training/strategies/strategy_utils.cpp`
- `src/training/optimizer/adam_optimizer.cpp`
- `src/training/rasterization/fastgs/optimizer/src/adam_api.cu`
- `src/training/kernels/mrnf_kernels.cu`

The exact revision and full source list are recorded in
`docs/dronegs/GPL_COMPONENTS.md`. Local `cuda/rasterization.cu` and
`cuda/trainer.cu` remain GPL-3.0-or-later; the linked native binary is
GPL-covered.

## Decision

Keep dev.17 as a versioned negative experiment, but reject its absolute
optimizer rates as the new quality anchor. The accepted Albagnac anchor remains
dev.16.

The result falsifies the assumption that optimizer constants transfer directly
between LichtFeld and DroneGS. The next phase must measure, per parameter
family, gradient distributions, Adam-normalized update magnitudes, and actual
parameter deltas on the same cameras. Equivalent DroneGS rates should then be
derived one family at a time before progressive SH or additional MRNF features.

Do not tag `dronegs-v0.5.0`; quality and speed parity remain open.
