# DroneGS native trainer

This directory contains the C++23/CUDA DroneAI Gaussian trainer. The
pre-dev.15 native implementation is original MIT code. Dev.15-dev.21 adapt
MRNF error weighting, cadence, long-axis split, weighted-Gumbel selection, and
edge-guidance and optimizer-schedule behavior from pinned LichtFeld inside two
explicitly GPL-3.0-or-later CUDA translation units; see
`docs/dronegs/GPL_COMPONENTS.md`.

Version `0.5.0-dev.23` adds an exact-pair LPIPS evaluation slice. It retains
the dev.22 training behavior and:

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
  `lichtfeld-absolute` profile, five exact one-family ablations, a strict
  DC-plus-opacity combination, and three intermediate-DC-plus-opacity
  calibration profiles, with dev16 retained as the default;
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
- writes per-view quality CSV data and can export exactly paired final
  lossless PPM predictions and RGB8 targets for an external LPIPS pass;
- provides a pinned, isolated LPIPS v0.1/AlexNet evaluator that writes
  per-view and aggregate results and atomically enriches the run manifest;
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
measured. Dev.21 swept DC rates `0.005`, `0.010`, and `0.020` with LichtFeld
opacity while retaining dev16 geometry. Dev.22 replays dev16, DC=0.010, and
DC=0.020 with the exact same dev.21 binary on the independent 1,065-view
Savères scene. At 1,000 steps, Savères dev16 reaches 16.65243 dB / 0.131453
SSIM, DC=0.010 reaches 16.83870 dB / 0.131405, and DC=0.020 reaches
16.79856 dB / 0.132098. Across Albagnac and Savères, DC=0.020 improves the
same-binary controls by +0.13101 dB and +0.001065 SSIM over 306 held-out
views, winning 303/306 PSNR views and 264/306 SSIM views. It is now the
recommended quality profile. Dev16 remains the default throughput profile
because both candidates increase training and wall time; dev.23 makes LPIPS
measurement reproducible but does not retroactively change that recommendation.
Decoded
images use a bounded LRU plus a bounded in-flight queue.
Albagnac measurements rejected multiple decode workers as the default because
CPU/GPU power contention outweighed the removed foreground wait. Reduced-IDCT
targets are also opt-in because their filtered pixels change the loss target
and still need held-out quality validation.
Pinned transfer buffers and asynchronous host-to-device copies are not retained:
the current Albagnac prototype measured only about 0.06 seconds of upload service
over 500 iterations. The binary identifies itself as
`dronegs-mrnf-exact-pair-lpips-prototype` and remains
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
matching LichtFeld. `--save-eval-images 1` writes final predictions and their
exact RGB8 targets with matching filenames and requires a non-zero held-out
stride.

## Exact-pair LPIPS evaluation

Build the isolated CPU evaluator once:

```bash
docker build -t dronegs-lpips:0.5.0-dev.23 \
  -f app1-colmap/dronegs/Dockerfile.lpips app1-colmap/dronegs
```

After a run created with `--save-eval-images 1`, evaluate it without touching
COLMAP or the native CUDA trainer:

```bash
docker run --rm \
  --mount type=bind,src=/absolute/run/path,dst=/run \
  dronegs-lpips:0.5.0-dev.23 \
  /run --device cpu
```

The command refuses incomplete/misaligned pairs, normalizes RGB inputs to
`[-1, 1]`, writes `evaluation/lpips.csv` and `evaluation/lpips.json`, and
updates the existing manifest atomically. The initial model-weight download is
a separately cached third-party artifact and must be tracked for reproducible
offline deployment.

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
`calibrated-dc-0.005-opacity`, `calibrated-dc-0.010-opacity`, and
`calibrated-dc-0.020-opacity` keep the LichtFeld opacity behavior and use the
named intermediate DC rate; all other parameter families remain exactly
dev16. The `0.020` profile is the recommended quality profile after two-scene
validation; `0.010` remains the best mean-PSNR candidate. `dronegs-dev16`
remains the default throughput profile until LPIPS and larger-scene throughput
gates pass.

The output directory must be empty and must not contain the source dataset.
