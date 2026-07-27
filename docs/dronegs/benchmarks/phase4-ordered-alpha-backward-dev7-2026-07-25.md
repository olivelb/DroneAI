# Phase 4 ordered-alpha backward result

Date: 2026-07-25
Status: DC/opacity gradient correctness sub-gate passed; not yet integrated
into the production trainer

## Scope

`0.5.0-dev.7` differentiates the validated front-to-back alpha renderer with
respect to:

- the three degree-zero spherical-harmonic/DC color parameters;
- the opacity logit.

It does not yet differentiate position, scale, rotation, projected covariance,
or higher-order spherical harmonics.

For a contributing splat `i`, the reverse pass carries the color `R(i+1)`
composited by all later splats. The alpha derivative is based on
`T(i) * (color(i) - R(i+1))`, where `T(i)` is transmittance immediately before
the splat. CUDA reconstructs `T(i)` in reverse from final residual
transmittance and `(1 - alpha(i))`. Alpha clamps, minimum contribution, color
clamps, and early-transmittance exit use the same decisions as the forward pass.

## Correctness evidence

- The CPU reference checks all DC and opacity parameters of a two-splat scene
  against central finite differences.
- CUDA is compared with the CPU reference on equal-depth splats, multiple tiles,
  non-black background, empty input, and early exit.
- Direct CUDA finite differences independently sample one DC and one opacity
  parameter.
- RGB and transmittance retain the existing `3e-5` forward tolerance.
- Gradient parity uses `4e-4` normally and `5e-4` for the high-opacity
  early-exit case; the direct CUDA finite-difference limits are `8e-4` for DC
  and `1.2e-3` for opacity.

All reference, CUDA parity, finite-difference, and existing convergence tests
pass. Atomic accumulation means repeated CUDA gradients are not promised to be
bitwise identical.

## Performance method

The optional benchmark times the complete public call, including allocation,
Gaussian upload, projection, CUB sorting/scans, tile construction, forward,
backward, gradient/RGB/transmittance readback, and deallocation.

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Temperature | 70-73 C |
| Build | Release, `sm_89`, CUDA 12.8.93, `--use_fast_math` |
| Image | 800 x 580 |
| Gaussians / visible | 1,025,093 / 1,025,093 |
| Evaluated / contributing pairs | 86,039,213 / 64,588,289 |
| Warm-up | one complete call per process, excluded |
| Repetitions | two five-run sets in opposite mode order |

## Result

| Complete public call | Combined median | Relative to forward |
|---|---:|---:|
| Forward | 35.190 ms | 1.00x |
| Forward + backward | 52.889 ms | 1.50x |
| Incremental backward cost | 17.699 ms | +50.30% |

One backward call measured 96.833 ms; the other nine measured between 50.628
and 55.427 ms. The order-balanced combined median is reported rather than
discarding the outlier silently.

Reproduce with:

```bash
cmake -S app1-colmap/dronegs -B /tmp/dronegs-dev7-bench \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DDRONEGS_BUILD_TESTS=OFF \
  -DDRONEGS_BUILD_BENCHMARKS=ON
cmake --build /tmp/dronegs-dev7-bench \
  --target dronegs_rasterization_cuda_benchmark
/tmp/dronegs-dev7-bench/dronegs_rasterization_cuda_benchmark \
  1025093 5 backward
```

## Integration decision

Accept the gradient implementation as the `0.5.0-dev.7` correctness
foundation, but do not route the trainer through the public validation API.
Doing so would recreate projection/sort/tile buffers every iteration, copy the
image gradient from the host, and copy all Gaussian gradients back to the host
before Adam.

The next sub-gate is a persistent device-resident training context that:

1. retains Gaussian, sort, tile, gradient, and Adam buffers;
2. produces the image-loss gradient on device;
3. launches ordered-alpha backward without host readback;
4. applies Adam on device;
5. demonstrates convergence before replacing the additive trainer path.

No `dronegs-v0.5.0` tag is created. Held-out photographic quality and
LichtFeld parity remain mandatory.
