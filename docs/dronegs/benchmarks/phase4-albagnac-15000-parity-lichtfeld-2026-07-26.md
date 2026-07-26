# Phase 4 — Albagnac 15k strict parity: DroneGS versus LichtFeld

Date: 2026-07-26  
DroneGS: `0.5.0-dev.38`  
LichtFeld Studio: pinned project runtime used by the benchmark  
Disposition: completed

## Question

Compare both trainers at 15,000 iterations under the same externally
controllable training contract, then score their final PLYs with the same
renderer and metric implementation.

## Strict shared contract

- exact same Albagnac dense COLMAP model and image files;
- 1,376 Mavic 3E RTK views and 1,025,093 initial dense points;
- same deterministic split (`scene_index % 8 == 0`):
  1,204 training views and 172 held-out views;
- 15,000 iterations and seed 42;
- MRNF strategy;
- SH degree 0 to 3, incremented every 1,000 steps;
- 1,500,000-splat cap;
- resize factor 4, maximum width 1,600 and tile size 4;
- same configured learning rates, regularizers and refinement schedule;
- error map and edge map enabled;
- final metrics on the exact same 172 held-out pairs.

The serialized LichtFeld configuration is
`configs/lichtfeld-albagnac-parity-15000-dev38.json`.

This is an exact input, budget, split and hyperparameter comparison. The
CUDA rasterizer, optimizer implementation, topology kernels and internal
floating-point behavior remain those of each engine: replacing those would
no longer compare the two actual implementations.

## Common-evaluator result

Both final PLYs are rendered by the corrected DroneGS dev.38 FastGS
evaluator at full SH3. PSNR, SSIM and LPIPS/AlexNet are then computed from
the same 172 target/prediction pairs.

| Metric | DroneGS dev.38 | LichtFeld 15k | LichtFeld advantage |
|---|---:|---:|---:|
| PSNR ↑ | 20.820227 dB | **21.513821 dB** | **+0.693594 dB** |
| SSIM ↑ | 0.567601 | **0.586497** | **+0.018896** |
| LPIPS ↓ | 0.434652 | **0.371055** | **−0.063597 (−14.63%)** |
| Final splats | 1,491,814 | 1,500,000 | +8,186 |
| Training compute | 2,993.767 s | **994.228 s** | **3.01× faster** |

LichtFeld wins all three common quality metrics and training throughput on
this test. DroneGS is therefore not yet at LichtFeld parity at 15k.

## Native LichtFeld check

LichtFeld's own evaluator reports `27.5119 dB / 0.8733 SSIM`. Those values
are retained as an implementation-native diagnostic but are not mixed into
the main table. The large gap to the common-renderer result demonstrates
that PLY interpretation and rasterizer conventions materially affect the
reported score; a fair cross-engine table must use one evaluator.

## Representative held-out view

The shared view is index 115 / COLMAP image 920,
`DJI_20230601172852_0921_V.JPG`.

| Engine | Per-view PSNR | Per-view SSIM |
|---|---:|---:|
| DroneGS | 20.866150 dB | 0.567197 |
| LichtFeld | **21.182177 dB** | **0.575910** |

## Timing

| Measurement | DroneGS | LichtFeld |
|---|---:|---:|
| Training compute | 2,993.767 s | 994.228 s |
| End-to-end trainer wall | 3,080.047 s | approximately 1,027.052 s |
| Average training rate | approximately 5.01 iter/s | 15.1 iter/s |

No COLMAP extraction, matching, mapping or bundle adjustment was rerun.

## Artifacts

- DroneGS run:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-15000`
- LichtFeld run:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38`
- LichtFeld final PLY:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38/splat_15000.ply`
- common LichtFeld evaluation:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38-cross-eval`
- common-evaluator LPIPS:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38-cross-eval/evaluation/lpips.json`
- representative LichtFeld comparison:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-parity-15000-dev38-cross-eval/preview.png`
- live/final dashboard:
  `http://localhost:3001/gaussian-progress`

## Consequence for the next optimization phase

The remaining target is now quantified: recover at least `+0.694 dB`,
`+0.0189 SSIM` and `−0.0636 LPIPS` while reducing the approximately 3×
training-time gap. The common renderer should remain frozen so future gains
cannot come from an evaluation change.
