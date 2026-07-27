# Phase 4 GAJAN fixed-topology development smoke

Date: 2026-07-24
Status: experimental sub-gate; not a LichtFeld parity result

## Scope

This report validates that the original DroneGS Phase 4 scaffold can load real
GAJAN cameras and JPEGs, project the initialized sparse Gaussians, propagate
photometric gradients, reduce a fixed anchor-image loss, and export the expected
PLY and run manifest.

It does not compare image quality with LichtFeld. The rasterizer uses normalized
additive weights rather than ordered alpha compositing, and only DC color plus
opacity are trainable.

## Environment

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| CUDA development/runtime image | NVIDIA CUDA 12.8.1, Ubuntu 24.04 |
| DroneGS version | `0.5.0-dev.1` working tree based on `d7ff0662c425b50eed90524f2b08eaac37a04cef` |
| Container image | `dronegs-dev:0.5.0-dev` |
| Dataset | `/home/olivier/droneAI-workspaces/gajan-r2s-smoke/dense` |
| Images | 25 JPEG, source 2400 x 1344, training 600 x 336 |
| Camera | one COLMAP PINHOLE camera |
| Sparse/final Gaussians | 9,324 / 9,324 |
| Seed | 42 |

The development manifests correctly carry the base revision plus `-dirty`.
A clean post-commit run is required for immutable benchmark provenance.

## Results

The reported loss is mean L1 over pixels covered by at least one projected
Gaussian in the first COLMAP image. Per-iteration progress loss refers to the
currently sampled camera and is therefore not expected to be monotonic.

| Run | Initial anchor L1 | Final anchor L1 | Reduction | Training | Wall |
|---|---:|---:|---:|---:|---:|
| 10 iterations | 0.1274932 | 0.1151457 | 9.68% | 0.0280 s | 1.1398 s |
| 500 iterations | 0.1274940 | 0.0983346 | 22.87% | 1.1865 s | 2.3434 s |

The 500-iteration kernel loop sustained about 421 iterations/s on this small
fixed-topology workload. This number is not comparable to LichtFeld's complete
5,000-iteration MRNF run because topology, compositing, loss, and optimized
parameter sets differ.

External development artifacts:

- `/home/olivier/droneAI-workspaces/dronegs-phase4-smoke-10-a/`
- `/home/olivier/droneAI-workspaces/dronegs-phase4-smoke-500-a/`
- `/home/olivier/droneAI-workspaces/dronegs-phase4-provenance-a/`

## Gate decision

The experimental gradient/convergence sub-gate passes:

- native CPU, CUDA gradient, and synthetic convergence tests pass;
- real GAJAN loss decreases without changing Gaussian count;
- the exported PLY and schema-valid manifest are produced;
- Git provenance is no longer `unknown` in the container.

The Phase 4 `0.5.0` exit gate remains open. Required next work:

1. ordered tile-based front-to-back alpha compositing;
2. anisotropic 3D covariance projection and geometry gradients;
3. DSSIM and progressive spherical harmonics;
4. held-out PSNR/SSIM/LPIPS against the pinned LichtFeld oracle;
5. bounded asynchronous image decode/cache for 1,000+ photographs.

No `dronegs-v0.5.0` tag is created from this result.
