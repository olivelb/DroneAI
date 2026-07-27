# Phase 4 anisotropic geometry backward result

Date: 2026-07-25
Status: public geometry-gradient correctness sub-gate passed; persistent
optimizer integration open

## Change

`0.5.0-dev.11` extends ordered-alpha backward from DC color and opacity to
position, three log-scales, and normalized `w,x,y,z` quaternion rotation.

CUDA first accumulates five adjoints per Gaussian for projected center `x,y`
and inverse conic `xx,xy,yy`. One thread per Gaussian then reverses:

1. inverse 2D covariance;
2. the piecewise spectral eigenvalue clamp;
3. projected covariance and perspective Jacobian;
4. camera-space position and world-to-camera rotation;
5. exponential scale and Gaussian rotation matrix;
6. quaternion normalization.

Visibility, support/tile selection, depth order, minimum contribution, alpha
clamp, and early exit are discrete stop-gradient operations. Geometry finite
differences therefore use a fixture that remains on one smooth branch.

The CPU reference computes geometry gradients by central differences. It sums
pixel differences before reduction in double precision, avoiding cancellation
from subtracting two complete float objectives.

## Correctness gates

- Public gradients cover DC, opacity, position, log-scale, and rotation.
- All 3 position, 3 log-scale, and 4 quaternion components pass direct CUDA
  finite differences on an anisotropic, non-unit-quaternion fixture.
- CPU/CUDA RGB, transmittance, statistics, DC, and opacity retain parity on
  rotated-camera, multi-tile, anisotropic, empty, and early-exit fixtures.
- CPU projection, depth stability, CUDA additive gradients, and both additive
  and ordered convergence suites pass.
- Ordered synthetic loss remains monotone from `0.346224` to `0.313348` over
  30 iterations.

The persistent trainer intentionally passes no geometry-gradient buffer in
dev.11. Its allocations, moments, and updates remain DC/opacity-only.

## Public-call benchmark

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Build | Release, `sm_89`, CUDA 12.8.93, `--use_fast_math` |
| Image | 800 x 580 |
| Gaussians / visible | 1,025,093 / 1,025,093 |
| Evaluated / contributing pairs | 83,847,359 / 63,516,885 |
| Method | two seven-run sets in opposite mode order |

One warm-up call per process is excluded. The combined median uses all 14
measured calls. The 271.656 ms backward outlier is retained.

| Complete public call | dev.10 | dev.11 | Change |
|---|---:|---:|---:|
| Forward | 44.934 ms | 40.528 ms | -9.8% |
| Forward + backward | 64.416 ms | 91.601 ms | +42.2% |
| Incremental backward | 19.482 ms | 51.073 ms | +162.2% |

Forward source is unchanged; its apparent improvement is run variance, not a
claimed optimization. The backward delta includes ten new output gradients,
per-pixel projected-conic atomics, the per-Gaussian reverse kernel, and host
readback of ten additional floats per Gaussian.

This public validation API allocates and transfers on every call. Dev.12 will
measure the persistent version, where gradients and Adam state stay on device.

## Real Albagnac regression

| Item | Value |
|---|---|
| Dataset | Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev11-geometry-api-500/` |

| Metric | dev.11 regression |
|---|---:|
| Initial anchor L1 | 0.20055936 |
| Final anchor L1 | 0.15530746 |
| Anchor reduction | 22.56% |
| End-to-end wall | 32.233 s |
| Throughput | 15.51 iterations/s |
| Trainer compute | 18.808 s |
| Foreground image wait | 12.045 s |
| JPEG decoder service | 29.365 s |
| Startup | 0.556 s |
| PLY export | 0.207 s |
| Cache evictions | 309 |
| Peak resident image cache | 267,264,000 bytes |

PLY SHA-256:
`d45bddd218887bcc90aa490e0c210f8dc351d7d6d21a2c3b86d8a1a5715f0ab3`.

No CUDA or host OOM occurred. Anchor loss matches dev.10 within `8e-7`, as
expected because persistent geometry optimization is disabled. Wall time is
6.3% higher than dev.10 because foreground image wait rose from 5.542 s to
12.045 s; trainer compute fell from 23.136 s to 18.808 s. This is a regression
smoke test, not a speed claim.

The run used the dev.11 working tree based on commit `3f0c98a`; the final
dev.11 commit contains the same tested source plus version and documentation.

## Decision

Accept the public anisotropic geometry backward foundation:

- all ten continuous geometry parameters have direct finite-difference
  coverage;
- discrete raster decisions are explicitly stop-gradient;
- the million-Gaussian public cost is measured;
- the real persistent trainer remains stable.

No `dronegs-v0.5.0` tag is created. Dev.12 must integrate persistent geometry
buffers, Adam moments, learning-rate schedules, and updates before geometry can
improve reconstruction quality.
