# Phase 4 anisotropic covariance forward result

Date: 2026-07-25
Status: CPU/CUDA anisotropic-forward correctness sub-gate passed; geometry
gradient sub-gate open

## Change

`0.5.0-dev.10` replaces one projected scalar sigma per Gaussian with a full
inverse 2D conic.

For each visible Gaussian, CPU and CUDA now:

1. normalize the `w,x,y,z` quaternion;
2. exponentiate the three independent log scales;
3. form `R_camera * R_gaussian * diag(scale)`;
4. apply the complete pinhole projection Jacobian, including the depth
   derivatives;
5. multiply the projected matrix by its transpose;
6. clamp both covariance eigenvalues to `[0.75², 8²]` pixels;
7. store the inverse conic and the two axis-aligned 2.5-sigma support radii.

The conic is used by culling, tile bounds, ordered forward composition, reverse
composition, the persistent trainer, and the public validation API. The
spectral clamp preserves the previous maximum 20-pixel support per screen axis.

## Correctness gates

- A horizontal non-uniform Gaussian projects to the expected 5.0 by 1.875
  pixel support radii.
- A 90-degree quaternion rotation swaps those axes and their conic
  coefficients.
- A 45-degree rotation produces a non-zero cross term.
- Non-unit quaternions are normalized; a zero quaternion is rejected.
- Extreme scales respect the maximum projected eigenvalue.
- CPU and CUDA forward RGB, transmittance, contribution statistics,
  DC gradients, and opacity gradients match with anisotropic Gaussians and a
  rotated camera.
- Existing depth stability, early exit, finite differences, and synthetic
  additive/ordered convergence tests pass.

The ordered synthetic loss remains monotone and changes from `0.346224` at
iteration 1 to `0.313348` at iteration 30.

## Public-call benchmark

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Build | Release, `sm_89`, CUDA 12.8.93, `--use_fast_math` |
| Image | 800 x 580 |
| Gaussians / visible | 1,025,093 / 1,025,093 |
| Evaluated / contributing pairs | 83,847,359 / 63,516,885 |
| Method | two seven-run sets in opposite mode order |

One complete warm-up call per process is excluded. The combined median is the
median of all 14 measured calls. One 113.878 ms backward outlier is retained.

| Complete public call | Isotropic dev.7 | Anisotropic dev.10 | Change |
|---|---:|---:|---:|
| Forward | 35.190 ms | 44.934 ms | +27.7% |
| Forward + backward | 52.889 ms | 64.416 ms | +21.8% |
| Incremental backward | 17.699 ms | 19.482 ms | +10.1% |

The anisotropic footprint evaluates 2.5% fewer pixel/splat pairs and accepts
1.7% fewer contributions in this synthetic scene. The remaining cost is
primarily quaternion, Jacobian, covariance, eigenspectrum, and larger projected
record work.

`ProjectedAlphaSplat` grows by 16 bytes. The two persistent million-item record
buffers therefore add about 31.3 MiB at Albagnac cardinality. Peak VRAM was not
resampled in this sub-gate.

## Real Albagnac run

| Item | Value |
|---|---|
| Dataset | Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev10-anisotropic-500/` |

| Metric | Anisotropic dev.10 |
|---|---:|
| Initial anchor L1 | 0.20055859 |
| Final anchor L1 | 0.15530697 |
| Anchor reduction | 22.56% |
| End-to-end wall | 30.316 s |
| Throughput | 16.49 iterations/s |
| Trainer compute | 23.136 s |
| Foreground image wait | 5.542 s |
| JPEG decoder service | 25.525 s |
| Startup | 0.780 s |
| PLY export | 0.222 s |
| Cache evictions | 309 |
| Peak resident image cache | 267,264,000 bytes |

PLY SHA-256:
`280271c5d944bd2772de77f6e3d93718787f237804f5322a26377d21fd69bd53`.

No CUDA or host OOM occurred. Compared with the clean dev.8 persistent
isotropic reference, wall time is 17.5% longer and end-to-end throughput is
14.9% lower. This comparison is a cost characterization, not a quality
comparison.

Initial Gaussians still have equal scales and identity rotations, so dev.10
only introduces the physically correct off-axis perspective covariance during
this fixed-topology run. Its anchor loss is consequently almost unchanged.
The quality value of anisotropy becomes material only after scale and rotation
can learn.

The run used the dev.10 working tree based on commit `27616b9`; the final
dev.10 commit contains the same tested source plus version and documentation
updates.

## Decision

Accept anisotropic covariance projection as the Phase 4 correctness
foundation despite its measured cost:

- the scalar-sigma model cannot reach 3DGS quality parity;
- CPU/CUDA behavior and safety clamps are tested;
- the million-Gaussian workload remains stable;
- the cost is explicit and can be optimized after geometry gradients are
  correct.

No `dronegs-v0.5.0` tag is created. Position, scale, and rotation gradients,
their Adam states and schedules, DSSIM, progressive SH, and held-out
LichtFeld-quality metrics remain open.
