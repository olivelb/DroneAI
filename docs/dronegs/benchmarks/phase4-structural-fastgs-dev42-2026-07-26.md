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

The port is accepted for continued dev.42 optimization. A 15,000-step run is
still gated on a second scene and a longer convergence pilot; the prior dev.40
15,000-step run must not be reused because it was intentionally aborted.
