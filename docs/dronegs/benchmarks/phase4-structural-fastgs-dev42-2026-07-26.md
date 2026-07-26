# Dev.42 structural FastGS backend

## Scope

Dev.42 ports the performance-critical FastGS execution structure into the
standalone DroneGS CUDA backend:

- inclusive-scanned buckets of 32 sorted tile instances;
- persistent bucket-to-tile indices;
- packed RGBA8 per-pixel checkpoints at bucket boundaries;
- per-pixel last-contributor and per-tile maximum-contribution bounds;
- one warp per bucket, one primitive per lane, with diagonal pixel-state
  propagation and register accumulation;
- shared-memory fused active-pixel L1 plus valid-window SSIM forward and
  analytical backward.

The implementation does not invoke LichtFeld. DroneGS retains its own COLMAP
reader, image cache/prefetch, deterministic view ordering, MRNF
growth/prune/reuse/noise lifecycle, optimizer, CLI, manifests, and process
orchestration. The adapted CUDA portions and linked native binary are recorded
as GPL-3.0-or-later in `docs/dronegs/GPL_COMPONENTS.md`.

## Validation

- CUDA/C++ build: release, portable architecture selection.
- Tests: 6/6 suites pass.
- Added fused-objective parity against the historical loss path.
- Added finite-difference probes for the fused image gradient.
- Source photos and COLMAP products were mounted read-only.

## Strict Albagnac pilot

Both runs used the undistorted Albagnac COLMAP dataset, seed 42, 1,000 steps,
resize factor 4, maximum width 1,600, cap 1.5 million, progressive SH degree
3, LichtFeld-absolute optimizer rates, LichtFeld pruning bounds, FastGS raster
semantics, identical held-out modulo-8 split, and no saved evaluation images.

| Metric | dev.41 cached backward | dev.42 structural FastGS | Change |
|---|---:|---:|---:|
| Training | 30.152 s | 20.290 s | -32.7% |
| Evaluation | 21.311 s | 19.608 s | -8.0% |
| Wall | 101.564 s | 89.859 s | -11.5% |
| Final loss | 0.211019 | 0.210574 | -0.000445 |
| Held-out PSNR | 19.03151 dB | 19.03852 dB | +0.00700 dB |
| Held-out SSIM | 0.495111 | 0.495167 | +0.000055 |
| Final Gaussians | 1,436,641 | 1,436,663 | +22 |

The small topology difference is expected from FastGS-compatible packed
checkpoint quantization perturbing gradient accumulation near deterministic
pruning thresholds. It does not indicate a quality regression.

Artifacts:

- control:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev41-control-1000-structbench/`
- dev.42:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-fastgs-struct-1000/`

## Decision

The port is accepted. The prior dev.40 15,000-step run remains excluded
because it was intentionally aborted.

## Strict Savères cross-scene pilot

The same 1,000-step contract was rerun on the independent Savères Mavic 3E
RTK reconstruction (1,066 input views, 134 held-out views).

| Metric | dev.41 cached backward | dev.42 structural FastGS | Change |
|---|---:|---:|---:|
| Training | 17.327 s | 14.823 s | -14.4% |
| Evaluation | 19.290 s | 16.125 s | -16.4% |
| Wall | 97.542 s | 87.345 s | -10.5% |
| Final loss | 0.220743 | 0.221071 | +0.000328 |
| Held-out PSNR | 17.73599 dB | 17.74749 dB | +0.01150 dB |
| Held-out SSIM | 0.329527 | 0.329526 | -0.000001 |
| Final Gaussians | 900,315 | 900,324 | +9 |

The speedup therefore generalizes to a second drone scene without a
meaningful quality change.

Artifacts:

- control:
  `/home/olivier/droneAI-workspaces/saveres-dronegs-dev41-control-1000-structbench/`
- dev.42:
  `/home/olivier/droneAI-workspaces/saveres-dronegs-dev42-fastgs-struct-1000/`

## Albagnac 15,000-step result

The accepted dev.42 backend was trained for the full 15,000-step strict
Albagnac contract. Its final PLY and the LichtFeld 15k reference PLY were
rendered by the same frozen DroneGS dev.38 FastGS evaluator on the same 172
held-out views. LPIPS v0.1/AlexNet used the exact RGB8 target/prediction pairs.

| Metric | DroneGS dev.42 | LichtFeld 15k | DroneGS − LichtFeld |
|---|---:|---:|---:|
| Common PSNR ↑ | 21.346178 dB | 21.513821 dB | -0.167643 dB |
| Common SSIM ↑ | **0.619733** | 0.586497 | **+0.033236** |
| Common LPIPS ↓ | **0.363027** | 0.371055 | **-0.008028 (-2.16%)** |
| Final splats | 1,499,885 | 1,500,000 | -115 |
| Training compute | **988.383 s** | 994.228 s | **-5.845 s (-0.59%)** |
| Trainer wall | 1,173.139 s | approximately **1,027.052 s** | +146.087 s |

Dev.42 reaches training-compute throughput parity and exceeds LichtFeld on
SSIM and LPIPS. Strict all-metric parity is not yet reached because PSNR
remains 0.168 dB lower. End-to-end wall time also remains 14.2% slower: the
remaining gap is outside the structural raster/backward kernels and is
dominated by DroneGS data loading/image waiting (175.36 s before and around
training).

Artifacts:

- training:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-fastgs-struct-15000/`
- common evaluation:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-fastgs-struct-15000-cross-eval/`
- exact-pair LPIPS:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-fastgs-struct-15000-cross-eval/evaluation/lpips.json`
- representative target/render pair:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-fastgs-struct-15000-cross-eval/preview.png`
- live/final dashboard:
  `http://localhost:3001/gaussian-progress`

The next optimization target is deliberately narrow: recover at least
0.168 dB PSNR without regressing SSIM/LPIPS, and remove the approximately
146-second host-side loading/waiting gap without changing the frozen common
evaluator.
