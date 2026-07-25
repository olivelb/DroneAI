# DroneGS native trainer

This directory contains the C++23/CUDA DroneAI Gaussian trainer. The
pre-dev.15 native implementation is original MIT code. Dev.15-dev.20 adapt
MRNF error weighting, cadence, long-axis split, weighted-Gumbel selection, and
edge-guidance and optimizer-schedule behavior from pinned LichtFeld inside two
explicitly GPL-3.0-or-later CUDA translation units; see
`docs/dronegs/GPL_COMPONENTS.md`.

Version `0.5.0-dev.20` is an experimental MRNF optimizer-combination slice. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, poses, images, and sparse points;
- decodes JPEG training images and scales pinhole intrinsics;
- stores decoded RGB as bytes in a lazy 256 MiB LRU cache;
- provides a bounded ordered JPEG prefetch queue with a configurable worker
  pool while retaining the measured one-slot/one-worker default;
- exposes an opt-in libjpeg reduced-IDCT path for reproducible decode A/B tests;
- defines a tested CPU oracle for depth-sorted front-to-back alpha composition;
- projects normalized 3D quaternion/scale covariances through the full pinhole
  Jacobian into bounded anisotropic 2D conics;
- provides a forward CUDA renderer with GPU projection, stable radix sorting,
  GPU-built 16x16 tile ranges, and shared splat batches that match the CPU
  alpha oracle;
- provides tested CPU and CUDA ordered-alpha gradients for DC color, opacity,
  position, three log-scales, and normalized quaternion rotation;
- reverses the projected inverse conic through spectral covariance clamping,
  perspective, camera transform, scale, and rotation;
- trains through a persistent ordered-alpha CUDA context with reusable
  projection, CUB, tile, gradient, and Adam buffers;
- computes a `0.8 * active-pixel L1 + 0.2 * (1 - SSIM)` objective and its
  analytical image gradient on CUDA, then runs ordered-alpha backward and Adam
  without copying Gaussian gradients through the host;
- retains projected-conic and position/scale/rotation gradients plus their
  Adam moments on device across iterations;
- accumulates normalized SSIM-error-weighted visibility between 200-step
  refinement windows, selects 7% above 0.003 through reproducible weighted
  Gumbel top-K, and splits along each parent's longest rotated 3D axis;
- accumulates Sobel luminance edge contribution inside existing training
  backward passes, normalizes positive scores by their median, and applies a
  0.25 edge-guidance factor without extra edge-render passes;
- preallocates Gaussian/gradient/Adam capacity to `--max-cap` and resets every
  selected parent and appended child's optimizer moments after a split;
- exposes the accepted `dronegs-dev16` quality-anchor profile, the experimental
  `lichtfeld-absolute` profile, five exact one-family ablations, and a strict
  DC-plus-opacity combination, with dev16 retained as the default;
- isolates Adam epsilon per parameter family so an ablation changes exactly
  one family's rate, schedule, spatial normalization, and epsilon;
- samples approximately 4,096 Gaussians deterministically at step 1, every
  fifth of training, and the final step, reporting gradient RMS, actual applied
  update RMS, parameter RMS, and component sample count for all five families;
- applies the selected position/scale schedules, constant
  DC/opacity/rotation rates, bounded log-scales, and quaternion renormalization;
- supports an explicit LichtFeld-compatible held-out stride that excludes
  validation views from every shuffled training schedule;
- computes full-frame PSNR and Gaussian 11x11 valid-padding SSIM on CUDA before
  and after training, with a tested CPU oracle;
- writes per-view quality CSV data and can export final lossless PPM
  predictions for an external LPIPS pass;
- initializes one Gaussian per sparse point;
- projects fixed Gaussians and rasterizes additive screen-space kernels on CUDA;
- back-propagates active-pixel L1 gradients;
- updates DC color, opacity, position, scale, and rotation with Adam in
  deterministic camera order;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- provides direct CPU objective/metric oracles, finite-difference DSSIM and
  renderer gradient tests, and end-to-end convergence tests.

It is not a LichtFeld replacement yet. The experimental training path now uses
front-to-back anisotropic ordered-alpha composition, while the additive path
remains only as a convergence control. Persistent training now optimizes
position, log-scale, and normalized rotation in addition to DC and opacity.
Topology, weighted Gumbel selection, and edge guidance now run, but
prune/replacement, noise injection, decay, compaction, and non-DC SH
coefficients remain absent. Only
`SIMPLE_PINHOLE` and `PINHOLE` cameras are accepted. Held-out PSNR/SSIM are now
measured. At 1,000 steps, dev.20's same-binary control reaches 17.5140 dB /
0.251635 SSIM. Opacity-only reaches 17.5211 dB / 0.251892 and improves SSIM on
130/172 views. DC-plus-opacity reaches the best PSNR, 17.6319 dB, while still
improving mean SSIM to 0.251785; however, SSIM regresses on 106/172 views and
its median per-view SSIM delta is negative. It is retained as a strong PSNR
candidate, not made default. The next calibration should test intermediate DC
rates with LichtFeld opacity. LPIPS remains unevaluated.
Decoded
images use a bounded LRU plus a bounded in-flight queue.
Albagnac measurements rejected multiple decode workers as the default because
CPU/GPU power contention outweighed the removed foreground wait. Reduced-IDCT
targets are also opt-in because their filtered pixels change the loss target
and still need held-out quality validation.
Pinned transfer buffers and asynchronous host-to-device copies are not retained:
the current Albagnac prototype measured only about 0.06 seconds of upload service
over 500 iterations. The binary identifies itself as
`dronegs-mrnf-optimizer-calibration-prototype` and remains
opt-in.

## Container build

From the repository root:

```bash
docker build -t dronegs-dev:0.5.0-dev -f app1-colmap/dronegs/Dockerfile .

docker run --rm --gpus all \
  --mount type=bind,src="$PWD",dst=/workspace \
  -w /workspace/app1-colmap/dronegs \
  dronegs-dev:0.5.0-dev \
  bash -lc 'cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release &&
            cmake --build build && ctest --test-dir build --output-on-failure'
```

The optional raster benchmark is built with
`-DDRONEGS_BUILD_BENCHMARKS=ON` and run as:

```bash
./build/dronegs_rasterization_cuda_benchmark 1025093 5
./build/dronegs_rasterization_cuda_benchmark 1025093 5 backward
```

The default mode measures the complete ordered-alpha forward call. The
`backward` mode measures forward plus DC/opacity/geometry backward. Both include
allocation, projection, sorting, tile construction, rendering, and host readback.

## Experimental topology-growth training run

```bash
./build/dronegs \
  --data-path /data/colmap-dense \
  --output-path /tmp/dronegs-run \
  --iter 1 --strategy mrnf --sh-degree 0 --max-cap 500000 \
  --resize-factor 4 --max-width 1600 --tile-mode 4 \
  --seed 42 --run-manifest /tmp/dronegs-run/trainer_run.json \
  --test-every 8 --save-eval-images 1
```

`--test-every 0` (the default) preserves the previous all-images training
behavior. With `--test-every 8`, scene indices `0, 8, 16, ...` are held out,
matching LichtFeld. `--save-eval-images 1` writes only final predictions and
requires a non-zero held-out stride.

Optional native tuning arguments are `--prefetch-depth`, `--decode-workers`,
and `--jpeg-idct-scale 0|1`. Their defaults are `1`, `1`, and `0`, preserving
the dev.8 decode behavior.

`--optimizer-profile dronegs-dev16` is the default and current quality anchor.
`--optimizer-profile lichtfeld-absolute` reproduces the rejected direct
LichtFeld-rate experiment for controlled calibration and telemetry.
The `lichtfeld-dc-only`, `lichtfeld-position-only`,
`lichtfeld-opacity-only`, `lichtfeld-scale-only`, and
`lichtfeld-rotation-only` values change exactly one family for reproducible
ablation.
`lichtfeld-dc-opacity` combines only the LichtFeld DC and opacity behaviors;
position, scale, and rotation remain exactly dev16.

The output directory must be empty and must not contain the source dataset.
