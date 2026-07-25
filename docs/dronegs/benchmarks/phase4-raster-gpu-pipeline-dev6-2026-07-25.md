# Phase 4 GPU projection and tile-pipeline benchmark

Date: 2026-07-25
Status: large-scene forward-throughput sub-gate passed; not a training or
LichtFeld quality-parity result

## Change

`0.5.0-dev.6` removes the ordered-alpha forward renderer's host projection and
host `vector<vector<...>>` tile construction. The replacement pipeline:

1. projects and culls Gaussians on CUDA;
2. creates deterministic `(positive depth bits, source index)` keys;
3. performs a stable CUB radix sort by depth;
4. scans per-splat tile counts with CUB;
5. duplicates and stably sorts `(tile, depth)` pairs on CUDA;
6. constructs tile start/end ranges on CUDA;
7. renders the existing 16x16 shared-memory tile kernel.

The source-order component makes equal-depth behavior explicit and
deterministic. No LichtFeld or GPL implementation source was copied.

## Method

The committed optional benchmark target generates a deterministic scene and
times the complete `render_alpha_tiled_cuda` call. The measurement includes
device allocation, host-to-device Gaussian transfer, projection, sorting,
tile construction, rendering, statistics, RGB/transmittance readback, and
deallocation.

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| GPU temperature during final comparison | 69-71 C |
| Image | 800 x 580 |
| Main Gaussian count | 1,025,093 |
| Visible Gaussians | 1,025,093 |
| Compiler | CUDA 12.8.93 / GCC 13.3 |
| Build | Release, `sm_89`, `--use_fast_math` |
| dev.5 control | commit `5dacf10` plus only the benchmark target |
| Warm-up | one complete call per process, excluded |
| Main repetitions | two five-run sets in opposite version order |

The main cardinality and raster dimensions match the Albagnac large-scene
prototype, but the generated Gaussian distribution is synthetic. This isolates
the raster pipeline and is not an image-quality benchmark.

## Main result

The two process orders were dev.5 then dev.6, followed by dev.6 then dev.5.
Combining the ten measured calls per version removes the systematic first-pair
warm-state difference.

| Metric | dev.5 host pipeline | dev.6 GPU pipeline | Change |
|---|---:|---:|---:|
| Combined median | 146.311 ms | 35.395 ms | -75.81% |
| Speedup | 1.00x | 4.13x | +3.13x |
| Evaluated pairs | 86,039,204 | 86,039,213 | +9 |
| Contributing pairs | 64,588,286 | 64,588,289 | +3 |

The nine evaluated-pair and three contributing-pair differences are respectively
about `1.0e-7` and `4.6e-8` of the totals. They arise where GPU projection
arithmetic lands on raster thresholds that were previously computed on the CPU.
The dedicated CPU/CUDA parity suite remains exact for visibility and pair
statistics and within `3e-5` for RGB/transmittance, including equal depths,
multi-tile coverage, input reversal, backgrounds, culling, and early exit.
Held-out photographic metrics remain a separate mandatory gate.

## Scale sensitivity

Single seven-run sets show the fixed cost of CUB allocation and sorting:

| Visible Gaussians | dev.5 median | dev.6 median | Result |
|---:|---:|---:|---:|
| 10,000 | 5.030 ms | 6.914 ms | dev.6 37.5% slower |
| 100,000 | 14.355 ms | 11.191 ms | dev.6 1.28x faster |
| 1,025,093 | 146.311 ms | 35.395 ms | dev.6 4.13x faster |

The implementation is therefore justified by the intended large drone scenes,
not by tiny scenes. A hybrid CPU/GPU threshold can be considered later if small
scene latency becomes product-critical; it is not added now because it would
duplicate the preparation path.

## Reproduction

Configure the optional target:

```bash
cmake -S app1-colmap/dronegs \
  -B app1-colmap/dronegs/build-bench-sm89 \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DDRONEGS_BUILD_TESTS=OFF \
  -DDRONEGS_BUILD_BENCHMARKS=ON
cmake --build app1-colmap/dronegs/build-bench-sm89 \
  --target dronegs_rasterization_cuda_benchmark
```

Run the main workload:

```bash
app1-colmap/dronegs/build-bench-sm89/dronegs_rasterization_cuda_benchmark \
  1025093 5
```

## Decision

Accept the GPU projection/binning/sort pipeline for `0.5.0-dev.6`:

- the large-scene forward call is materially faster in an equal Release build;
- deterministic equal-depth ordering and multi-tile parity are tested;
- the benchmark is reproducible and opt-in;
- small-scene overhead and threshold-level floating differences are explicit.

No `dronegs-v0.5.0` tag is created. The trainer still uses the additive
backward path. The next correctness increment is ordered-alpha backward,
followed by anisotropic covariance and held-out photographic quality parity.
