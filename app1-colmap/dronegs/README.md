# DroneGS native trainer

This directory contains the C++23/CUDA DroneAI Gaussian trainer. The
pre-dev.15 native implementation is original MIT code. Dev.15-dev.21 adapt
MRNF error weighting, cadence, long-axis split, weighted-Gumbel selection, and
edge-guidance and optimizer-schedule behavior from pinned LichtFeld inside two
explicitly GPL-3.0-or-later CUDA translation units; see
`docs/dronegs/GPL_COMPONENTS.md`.

Version `0.5.0-dev.44` keeps dev.31's deterministic exact two-neighbour KNN
scale initialization and dev.32's live SH-derived `[0,4]` render color, then
adds dev.35 profiles that retain the dev.34 scale schedule while delaying
stronger rotation updates until 40% of training. Dev.36 adds homodirectional
per-pixel absolute projected-center gradients to MRNF's deterministic split
ranking to recover detail hidden by signed gradient cancellation. Dev.40
decouples optimizer and raster profiles and recycles pruned slots directly
on GPU when the Gaussian cap is full. Dev.41 caches forward transmittance and
active ranges. Dev.42 replaces the FastGS compatibility emulation with a
structural backend: 32-instance buckets, packed per-pixel checkpoints,
one-warp-per-bucket backward traversal, tile contribution early-out, and
shared-memory fused L1/SSIM forward/backward. Dataset loading, camera
selection, MRNF lifecycle, optimizer, CLI, manifests, and process
orchestration remain native DroneGS code and do not invoke LichtFeld.
Dev.43 replaces the fixed 256 MiB decoded-image LRU budget with an
auto-sized RGB8 budget: enough for the complete resized scene when it fits,
bounded between 256 MiB and 2 GiB. This removes repeated JPEG decoding on
thousand-view datasets while keeping host memory use explicit and bounded.
Dev.44 adds an opt-in `--topology-cooldown N`: topology refinement stops
after `iterations - N`, leaving the final `N` steps for fixed-topology
optimizer convergence without increasing the training budget. Its default is
zero, which preserves the dev.43 lifecycle exactly.
Dev.37 adds opt-in compensated screen-space filter ablations with exact
covariance/opacity gradients. Dev.38 adds a coupled FastGS compatibility
profile covering `0.3 I` projected covariance dilation, extended-FOV
Jacobian clamping, opacity-dependent support, the `0.999` fragment-alpha
ceiling, and matching analytical backward. It also imports binary Gaussian
PLY models for direct same-split renderer/model cross-evaluation. A
balanced local KD tree gives each COLMAP point an isotropic scale adapted to
its local density and bounded by robust scene extents. A local build detects
its visible NVIDIA GPU through CMake's `native` mode; the `portable` preset
emits a CUDA 12.8 runtime-selected fat binary for Turing through Blackwell. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, poses, images, and sparse points;
- optionally initializes from an existing binary 3DGS PLY through
  `--initial-ply`, preserving DC, SH, opacity, scale, and quaternion fields;
- initializes splat scales from the two nearest COLMAP neighbours instead of
  one scene-wide spacing estimate;
- keeps SH color and gradients live up to four while display output remains
  bounded independently;
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
- provides an opt-in structural FastGS raster path with scanned 32-instance
  buckets, RGBA8 pixel checkpoints, per-tile contribution bounds, a
  warp-cooperative backward pass, and fused tiled L1/SSIM kernels;
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
  DC-plus-opacity combination, three intermediate-DC-plus-opacity calibration
  profiles, and three post-KNN opacity-rate points, with dev16 retained as the
  command-line default, `calibrated-dc-0.010-opacity-0.096` recommended for
  balanced quality, and dev.34 scale/rotation profiles available for opt-in
  structure-oriented training; dev.38 adds the selected FastGS-compatible
  quality-parity profile;
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
- stores and exports all 45 non-DC degree-3 SH values, evaluates degrees 0–3
  in matching CPU/CUDA order, and optimizes only the currently active band;
- starts every run at degree 0 and activates one degree every
  `--sh-degree-interval` steps (1,000 by default) up to `--sh-degree`;
- injects deterministic opacity-weighted means noise during the MRNF window;
- prunes transparent, degenerate, non-finite, excessive-scale, and robust
  spatial-outlier Gaussians every 200 steps through iteration 28,500;
- compacts survivors and every persistent Adam moment into a dense prefix,
  accounts for children that reuse freed slots, and grows only through
  iteration 15,000;
- applies the pinned MRNF opacity and scale decays after each refinement;
- initializes one Gaussian per sparse point;
- projects fixed Gaussians and rasterizes additive screen-space kernels on CUDA;
- back-propagates active-pixel L1 gradients;
- updates DC color, opacity, position, scale, and rotation with Adam in
  deterministic camera order;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- provides direct CPU objective/metric oracles, finite-difference DSSIM and
  renderer gradient tests, and end-to-end convergence tests.

It is not the production default yet. The experimental training path now uses
front-to-back anisotropic ordered-alpha composition, while the additive path
remains only as a convergence control. Persistent training now optimizes
position, log-scale, and normalized rotation in addition to DC and opacity.
Topology, weighted Gumbel selection, and edge guidance now run, but
checkpoint/resume, operational acceptance, and full speed/VRAM parity remain
open. Same-split Albagnac PSNR/SSIM parity is reached in dev.38. Only
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
`dronegs-mrnf-complete-lifecycle-prototype` and remains
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

The default `DRONEGS_CUDA_ARCHITECTURES=native` asks CMake/nvcc to detect every
GPU visible while configuring. It is appropriate for local builds and emits
only the detected architecture. A headless or distributable build should use:

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DDRONEGS_CUDA_ARCHITECTURES=portable
```

The portable preset contains real cubins for `75`, `80`, `86`, `87`, `89`,
`90`, `100`, `101`, and `120`. The NVIDIA driver selects the matching cubin
automatically at runtime. An explicit CMake list
such as `-DDRONEGS_CUDA_ARCHITECTURES="86-real;89-real"` remains supported.
Passing `-DCMAKE_CUDA_ARCHITECTURES=...` directly takes precedence.

Dev.27 introduced compact projected sorting. The projected depth sort carries
48-byte render records through CUB while SH bases stay in a separate
source-indexed buffer. Tile bounds are reconstructed by lightweight coalesced
kernels. This keeps native Ada device link below the 48 KiB static
shared-memory limit without a full-record gather and reduces the persistent
projected-depth workspace by 112 bytes per reserved Gaussian versus dev.26.
Dev.30 uses CUB's public default stable radix dispatch on every target and does
not impose an architecture-specific register ceiling.
Set `-DCMAKE_CUDA_ARCHITECTURES=52` only to reproduce the dev.25/dev.26
PTX-JIT baseline.

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
docker build -t dronegs-lpips:0.5.0-dev.30 \
  -f app1-colmap/dronegs/Dockerfile.lpips .
```

After a run created with `--save-eval-images 1`, evaluate it without touching
COLMAP or the native CUDA trainer:

```bash
docker run --rm \
  --mount type=volume,src=dronegs-torch-cache,dst=/root/.cache/torch \
  --mount type=bind,src=/absolute/run/path,dst=/run \
  dronegs-lpips:0.5.0-dev.30 \
  --evaluation-dir /run/evaluation \
  --manifest /run/trainer_run.json \
  --device cpu
```

The command refuses incomplete/misaligned pairs, normalizes RGB inputs to
`[-1, 1]`, writes `evaluation/lpips.csv` and `evaluation/lpips.json`, and
updates the existing manifest atomically. The initial model-weight download is
cached in the named `dronegs-torch-cache` volume; it remains a separately
tracked third-party artifact for reproducible offline deployment. Atomic
updates preserve the existing manifest owner and mode, and new result files
inherit the evaluation directory owner with mode `0644`, including when the
container runs as root.

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
