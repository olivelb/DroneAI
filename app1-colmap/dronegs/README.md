# DroneGS native trainer

This directory contains the C++23/CUDA DroneAI Gaussian trainer. The
pre-dev.15 native implementation is original MIT code. Dev.15-dev.21 adapt
MRNF error weighting, cadence, long-axis split, weighted-Gumbel selection, and
edge-guidance and optimizer-schedule behavior from pinned LichtFeld inside two
explicitly GPL-3.0-or-later CUDA translation units; see
`docs/dronegs/GPL_COMPONENTS.md`.

Version `0.5.0-dev.64` schedules four independent FastGS backward buckets per
CUDA block while retaining one 32-thread NVIDIA warp per bucket. The
per-bucket traversal, checkpoint state and derivative equations are unchanged,
and the launch uses only portable CUDA warp primitives supported by the
project's Turing-through-Blackwell targets. On RTX 3090, two fixed-topology
5.1 M-Gaussian runs reduce sampled late backward by about 12% and mean native
training by 2.53%, with equivalent held-out quality. Performance on other GPU
architectures remains a separate qualification. Two exact-commit 30,000-step
HQ runs reduce mean native training by 1.34% and wall time by 1.32% from
dev.63. Their mean SSIM delta is `-0.001618`, pixel-weighted SSIM improves,
and the existing `0.002` non-regression threshold remains unchanged. Version
`0.5.0-dev.63` specializes SH Adam for the only valid active
coefficient counts (3, 8 and 15) and stores every SH parameter's first and
second moments as one `float2`. Runtime bytes and checkpoint format v5 remain
unchanged; checkpoint capture keeps the interleaved snapshot directly and
serialization/reload converts in bounded chunks. Two fixed-topology 5.1
M-Gaussian runs reduce mean native training by 4.38% from dev.62 while sampled
SH Adam falls from about 12.49 to 10.98 ms with equivalent held-out quality.
The exact-commit 30,000-step HQ gate reduces native training by 2.66% and wall
time by 2.71%; aggregate PSNR and SSIM improve and weighted SSIM remains inside
the established long-run non-regression envelope.
Version `0.5.0-dev.62` lays out scalar Adam as one 16-lane subgroup per
Gaussian. The four quaternion lanes normalize their updated rotation through
warp shuffles before the kernel returns, eliminating the following full-model
normalization pass. Two fixed-topology 5.1 M-Gaussian runs reduce mean training
by 4.77% and wall time by 4.51% from dev.61, with equivalent held-out quality.
The exact-commit 30,000-step HQ gate reduces native training by another 3.71%
and wall time by 3.66%; PSNR/SSIM remain inside the long-run non-regression
envelope.
Version `0.5.0-dev.61` rejects a Gaussian before SH and exact covariance only
when an opacity-one, maximum-axis conservative screen-space bound cannot
overlap the image. The prefilter has no cross-iteration cache and therefore
remains valid while position, rotation and scale keep changing. Two
fixed-topology 5.1 M-Gaussian runs reduce mean training by 3.77% from dev.60
with equivalent held-out quality. The exact-commit 30,000-step HQ gate reduces
native training by 2.49% and wall time by 2.44%, with PSNR/SSIM inside the
established non-regression envelope. Version `0.5.0-dev.60` consumes raw FastGS appearance derivatives directly in
scalar and SH Adam, avoiding expanded gradient buffers and their per-frame
clear. Two fixed-topology 5.1 M-Gaussian runs reduce mean training another
7.74% from dev.59 with equivalent held-out quality. Version `0.5.0-dev.59`
replaces per-source/tile color-SH and opacity-SH atomics in the structural
FastGS backward pass with one active-only expansion per Gaussian. A two-run
5.1 M-Gaussian cell benchmark reduces mean training time by 9.69% and sampled
late raster backward by 35.3%, with equivalent held-out quality. Version
`0.5.0-dev.58` captures immutable full-state
checkpoint snapshots on the training thread, then performs checksum, durable
write and atomic publish on one bounded background writer. Training never has
more than one snapshot in flight, write failures remain fatal, and the manifest
separates snapshot, wait and background-write timings. DroneAI caps standard
Fast/Normal/HQ runs at respectively one, two and three checkpoints. Version
`0.5.0-dev.57` stops
producing Sobel/error maps, per-frame refinement
weights and persistent refinement statistics as soon as no later topology
refinement can consume them. Geometry gradients and every optimizer update
remain active through the final iteration. On GAJAN, a fixed-topology
micro-benchmark reduces mean training time by 14.4%; complete Fast and Normal
runs improve by 0.6% and 0.2% respectively with held-out quality inside
retained run variation. Dev.56 reduces each fused L1/SSIM tile locally before updating
the global loss and active-pixel counters. On GAJAN, two Fast runs reduce mean
training time from 22.966 to 17.337 seconds and two Normal runs reduce it from
68.203 to 55.307 seconds, without a held-out quality regression or a larger
Gaussian population. Dev.55 updates each Gaussian's 14 scalar Adam parameters with
independent CUDA work items and normalizes rotations in a following kernel.
Two GAJAN Normal runs reduce mean training time from 73.306 to 68.203 seconds
while preserving held-out quality and the approximately 5.0 GiB VRAM envelope.
Dev.54 limits the required tile/depth radix sort to all depth
bits plus the tile-identifier bits actually used by the current image. Two
GAJAN Fast runs reduce mean training time by another 2.8% without a held-out
quality regression. Two Normal runs complete in 73.306 seconds mean training,
9.5% faster than dev.52, at approximately 5.0 GiB VRAM. Dev.53 removes a
redundant global depth sort before the
required tile/depth radix sort in persistent training, removes its two output
buffers and decomposes preprocessing telemetry into five measurable substages.
Two GAJAN Fast runs reduce mean wall time from 29.334 to 27.080 seconds while
preserving PSNR/SSIM and topology within retained run variation. Dev.52
distributes every active color-SH and opacity-SH Adam
coefficient over independent CUDA threads instead of updating as many as 60
coefficients serially in one thread per Gaussian. It preserves the same
equations, moments, progressive-SH boundary and default-stream ordering, and
adds no persistent VRAM allocation. GAJAN Fast improves from 38.985 to a
29.334-second two-run median; Normal improves from 162.594 to 84.472 seconds
while its PSNR and SSIM increase slightly. Version dev.51 adds staggered
CUDA-event telemetry for preprocessing,
rasterization, objective, backward and optimizer work at six sampled steps.
Sampling is deliberately offset from the existing optimizer-statistics
readback, so observed Adam cost is not inflated by diagnostic atomics. The
GAJAN Fast qualification identifies SH3 Adam and projection/sort/binning as
the dominant late-run GPU costs; the raster kernel itself is no longer the
primary optimization target. The preceding dev.50 removes three per-step
device-to-host metric readbacks
between the 20 progress reports emitted by a run. Loss, active-pixel and SSIM
validation remains fail-closed through a sticky device error flag that is
read at the next report and at the final iteration. The bounded ordered-alpha
rasterizer also minimizes each projected conic over candidate tile rectangles
and rejects only tiles that provably cannot contain a contributing pixel. This
exact culling stays disabled in the structural FastGS path because changing
its candidate stream would change packed checkpoint segmentation. Same-process
CUDA tests require deferred/synchronous training parity and exact contributing
pair parity.

Dev.49 keeps dev.31's deterministic exact two-neighbour KNN
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
bounded between 256 MiB and a configurable ceiling whose default is 2 GiB.
This removes repeated JPEG decoding on thousand-view datasets while keeping
host memory use explicit and bounded.
Dev.44 adds an opt-in `--topology-cooldown N`: topology refinement stops
after `iterations - N`, leaving the final `N` steps for fixed-topology
optimizer convergence without increasing the training budget. Its default is
zero, which preserves the dev.43 lifecycle exactly.
Dev.45 adds an opt-in `--photometric-finish N` and
`--photometric-mse-percent P`. During the final `N` iterations the objective
linearly transitions from the existing `0.8 L1 + 0.2 DSSIM` objective toward
an active-pixel MSE contribution whose final weight is `P%`. Both native CLI
defaults are zero, preserving dev.44 training math and cost outside explicit
photometric convergence ablations. The DroneAI pipeline selects the validated
1,000-step cooldown and photometric finish explicitly.
Dev.46 adds atomic versioned full-state checkpoints, strict scene/config
fingerprints, deterministic resume, deliberate pause exit code 75, and
held-out deployment canaries at the Python orchestration boundary.
Dev.47 adds checksum-protected checkpoint V3 publication, a versioned native
profile registry, strict dataset/binary/PLY identity on reuse, and a
deterministic `spatial-block` held-out policy with an optional guard ring.
Production V1 deliberately retains modulo parity while custom/V2 experiments
measure spatial generalization.
Dev.48 turns `tile_mode` into real source-image crops with crop-relative
intrinsics and grouped train/test assignment. Geographic block datasets may
also provide `image_regions.tsv`; DroneGS composes each base crop with
`tile_mode` while decoding the untouched source JPEG. Dataset identity v3
binds the crop contract to checkpoint and result reuse. Dev.48 also adds the explicitly
scoped `opacity-SH-v1` capability from the opacity-only FAGK ablation: SH
degrees 1 through 3 learn
view-dependent opacity-logit residuals, persist them as `opacity_sh_*` PLY
properties, and render them consistently in the native and orthomosaic CUDA
paths. Scale and rotation remain view-independent and are not claimed as full
FAGK. This bounded variant follows the opacity-only ablation from
[TOrtho-Gaussian](https://doi.org/10.1080/10095020.2026.2622788); it is kept
separate from the paper's view-dependent scale and rotation extensions.
Dev.49 can instead derive each initial scale from the actual crop-relative
camera projections, with a configurable screen-space ceiling. Its adaptive
growth and topology/noise boundary are computed from the requested iteration
budget, reach the hard resident capacity deterministically in the final growth
window, and preserve the local-KNN path for immutable historical profiles.
The optimizer uses the mixed analytical gradient, while per-step loss
telemetry intentionally remains the baseline L1+DSSIM value for direct
cross-run comparison. Exact mixed objective values remain available through
the evaluation API and its CUDA finite-difference tests.
Dev.37 adds opt-in compensated screen-space filter ablations with exact
covariance/opacity gradients. Dev.38 adds a coupled FastGS compatibility
profile covering `0.3 I` projected covariance dilation, extended-FOV
Jacobian clamping, opacity-dependent support, the `0.999` fragment-alpha
ceiling, and matching analytical backward. It also imports binary Gaussian
PLY models for direct same-split renderer/model cross-evaluation. A
balanced local KD tree gives each COLMAP point an isotropic scale adapted to
its local density and bounded by robust scene extents. A local build detects
its visible NVIDIA GPU through CMake's `native` mode; the `portable` preset
emits a CUDA 12.9 runtime-selected fat binary for Turing through Blackwell. It:

- parses trainer CLI contract v1;
- reads COLMAP binary cameras, poses, images, and sparse points;
- optionally initializes from an existing binary 3DGS PLY through
  `--initial-ply`, preserving DC, SH, opacity, scale, and quaternion fields;
- initializes splat scales from the two nearest COLMAP neighbours instead of
  one scene-wide spacing estimate;
- keeps SH color and gradients live up to four while display output remains
  bounded independently;
- learns opacity-SH residuals alongside progressive color SH and preserves
  them through topology changes and checkpoint V5;
- decodes JPEG training images and scales pinhole intrinsics;
- expands tile modes 2 and 4 into crop-relative training views without
  leaking a source photograph across train/test partitions;
- reduces oversized crop targets with an area filter that integrates
  fractional source-pixel coverage instead of point-sampling them;
- stores decoded RGB as bytes in an auto-sized scene cache with a 256 MiB
  floor and a configurable 2 GiB default ceiling;
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
  refinement windows, selects candidates above 0.003 through reproducible
  weighted Gumbel top-K, and splits along each parent's longest rotated 3D
  axis. The default selection remains 7%; resident HQ blocks can explicitly
  target their planned `--max-cap` by adapting the fraction between 7% and
  25% through the last growth window;
- accumulates Sobel luminance edge contribution inside existing training
  backward passes, normalizes positive scores by their median, and applies a
  0.25 edge-guidance factor without extra edge-render passes;
- preallocates Gaussian/gradient/Adam capacity to `--max-cap` and resets every
  selected parent and appended child's optimizer moments after a split;
- exposes a versioned optimizer registry: `reference-absolute` is the validated
  production optimizer, `dronegs-dev16` is the deprecated native CLI default
  retained for compatibility, the neutral
  `reference-absolute-absgrad025/050` candidates change only MRNF growth
  ranking, and the other `reference-*`, calibrated and dev.34–38 profiles
  remain explicit experiments rather than silent fallbacks;
- isolates Adam epsilon per parameter family so an ablation changes exactly
  one family's rate, schedule, spatial normalization, and epsilon;
- samples approximately 4,096 Gaussians deterministically at step 1, every
  fifth of training, and the final step, reporting gradient RMS, actual applied
  update RMS, parameter RMS, and component sample count for all five families;
- applies the selected position/scale schedules, constant
  DC/opacity/rotation rates, bounded log-scales, and quaternion renormalization;
- supports an explicit LichtFeld-compatible held-out stride that excludes
  validation views from every shuffled training schedule;
- supports a deterministic central spatial block and training guard ring,
  recording training, held-out and ignored camera counts;
- removes native-image subtiles with no projected sparse-Gaussian support
  before scheduling, while rejecting an entirely unsupported dataset;
- computes full-frame PSNR and Gaussian 11x11 valid-padding SSIM on CUDA before
  and after training, with a tested CPU oracle. It preserves the historical
  equal-view canary and also records globally pixel-weighted PSNR/SSIM plus
  per-tile dimensions and MSE, so small resident edge crops cannot silently
  dominate product-level quality analysis;
- writes per-view quality CSV data and can export exactly paired final
  lossless PPM predictions and RGB8 targets for an external LPIPS pass;
- provides a pinned, isolated LPIPS v0.1/AlexNet evaluator that writes
  per-view and aggregate results and atomically enriches the run manifest;
- stores and exports all 45 non-DC degree-3 SH values, evaluates degrees 0–3
  in matching CPU/CUDA order, and optimizes only the currently active band;
- starts every run at degree 0 and activates one degree every
  `--sh-degree-interval` steps (1,000 by default) up to `--sh-degree`;
- injects deterministic opacity-weighted means noise only during the
  run-scaled topology-growth window;
- prunes transparent, degenerate, non-finite, excessive-scale, and robust
  spatial-outlier Gaussians every 200 steps through the configured or adaptive
  refinement window;
- compacts survivors and every persistent Adam moment into a dense prefix,
  accounts for children that reuse freed slots, and grows only through
  the first half of the operator-selected iteration budget;
- applies the pinned MRNF opacity and scale decays after each refinement;
- initializes one Gaussian per sparse point;
- projects fixed Gaussians and rasterizes additive screen-space kernels on CUDA;
- back-propagates active-pixel L1 gradients;
- updates DC color, opacity, position, scale, and rotation with Adam in
  deterministic camera order;
- exports a DroneAI-compatible binary PLY;
- writes a run-manifest-v1 document;
- atomically checkpoints every parameter, Adam moment, topology statistic,
  schedule and deterministic state, with strict dataset/config fingerprints;
- resumes compatible interrupted runs and supports deliberate checkpoint
  canaries via `--stop-after`;
- provides direct CPU objective/metric oracles, finite-difference DSSIM and
  renderer gradient tests, and end-to-end convergence tests.

DroneGS is now the sole production Gaussian backend. The immutable V1 recipe
is derived from the validated Albagnac 15,000-step dev.45 acceptance run, which
reaches 22.175919 dB PSNR, 0.642557 SSIM, and 0.325408 LPIPS in 972.731
training seconds. The deterministic LichtFeld control reaches 21.513821 dB,
0.586497, and 0.371055 in 994.228 seconds under the same frozen evaluator.
The control is historical and is not a runtime/build dependency. The additive
renderer remains only as a convergence control. Full-state checkpoint/resume,
completed manifests, held-out PSNR/SSIM canaries and PLY outputs are atomic
pipeline gates. Only
`SIMPLE_PINHOLE` and `PINHOLE` cameras are accepted. Held-out PSNR/SSIM are now
measured. Dev.21 swept DC rates `0.005`, `0.010`, and `0.020` with LichtFeld
opacity while retaining dev16 geometry. Dev.22 replays dev16, DC=0.010, and
DC=0.020 with the exact same dev.21 binary on the independent 1,065-view
Savères scene. At 1,000 steps, Savères dev16 reaches 16.65243 dB / 0.131453
SSIM, DC=0.010 reaches 16.83870 dB / 0.131405, and DC=0.020 reaches
16.79856 dB / 0.132098. Across Albagnac and Savères, DC=0.020 improves the
same-binary controls by +0.13101 dB and +0.001065 SSIM over 306 held-out
views, winning 303/306 PSNR views and 264/306 SSIM views. It became the
recommended quality profile at that development point. Dev16 remained the
low-level throughput profile because both candidates increased training and
wall time; dev.23 made LPIPS measurement reproducible but did not
retroactively change that historical recommendation.
Decoded
images use a bounded LRU plus a bounded in-flight queue.
Albagnac measurements rejected multiple decode workers as the default because
CPU/GPU power contention outweighed the removed foreground wait. Reduced-IDCT
targets are also opt-in because their filtered pixels change the loss target
and still need held-out quality validation.
Pinned transfer buffers and asynchronous host-to-device copies are not retained:
the current Albagnac prototype measured only about 0.06 seconds of upload service
over 500 iterations. The native CLI keeps conservative historical defaults for
backward compatibility. The Python pipeline applies
`DRONEGS_PRODUCTION_PROFILE_V1` explicitly and records its requested and
effective configuration in `trainer_run.json`.

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

`--tile-mode 4` expands every photograph into four independently decoded 2x2
crops and adjusts the pinhole principal point for each crop. The width ceiling
therefore applies per crop: a 6000x4000 source can train on four native
3000x2000 views with `--max-width 4096`. Mode `2` splits the longest image axis;
mode `1` keeps the full-frame behavior. Train/test assignment remains grouped
by source photograph.

`--adaptive-native-crop-tiles 1` is an experimental resident-facade policy.
The configured tile mode becomes a maximum: a native crop uses 1, 2, or 4
tiles so that each tile stays within the full-sensor pixel budget implied by
that maximum. Full images therefore preserve the existing memory envelope,
while small boundary crops retain useful spatial context instead of being
split into four tiny views. The default remains `0` until controlled facade
canary and VRAM evidence support promotion.

Compare the fixed and adaptive modes only with two completed manifests from
the same native binary and otherwise identical scientific parameters:

```bash
python tools/compare_gaussian_crop_tiling_runs.py \
  fixed/trainer_run.json adaptive/trainer_run.json \
  --output crop-tiling-comparison.json
```

`--adaptive-growth-target 1` is used by area/GSD-planned resident blocks and
can also be selected explicitly for short custom runs. It recomputes the
growth fraction needed to approach `--max-cap` by the last 200-step boundary
strictly inside the first half of any requested iteration budget (3,600,
7,400, and 14,800 for 7,500, 15,000, and 30,000 iterations). Each request is
clamped to 7–50%; the higher ceiling lets sparse preview blocks reach their
requested capacity while the hard cap remains exact. The trainer emits the
fraction and capacity target in every topology-refinement event, freezes
topology afterwards, and stops deterministic position noise at the same
boundary so the second half is genuine fixed-topology convergence. The final
adaptive refinement reserves the minimum split budget so Gaussians pruned in
that window are replaced before topology freezes. Its default is `0`, so
existing standalone and versioned recipes keep the fixed 7% schedule and
their configured pruning window.
The resident HQ wrapper passes a pre-filter cap sized for 98% retention and
records both that training target and the strict GSD-backed retained target;
the native trainer still treats `--max-cap` as an exact hard ceiling.

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
`--host-image-cache-mib`, and `--jpeg-idct-scale 0|1`. Their defaults are
`1`, `1`, `2048`, and `0`, preserving the dev.8 decode behavior. The requested
host-cache bound and its effective byte capacity are recorded in the run
manifest. Changing the bound is an operational performance experiment and
requires exact final-PLY parity before promotion.

Checkpoint cadence is governed by the existing `--checkpoint-every` option.
The performance comparator may vary it together with cache and prefetch
tuning, but still requires the same trainer binary, scientific parameters and
exact final PLY SHA-256. A longer interval therefore cannot be promoted from
wall time alone; its recovery-point trade-off remains an operational decision.

Native-code optimizations are qualified separately with
`tools/compare_gaussian_binary_regression.py`. It permits different trainer
binaries and checkpoint locations, but requires the same dataset, every
scientific parameter, final PLY SHA-256, loss, PSNR and SSIM. This prevents an
I/O or topology optimization from being accepted on speed alone.

The manifest separates foreground image wait, cumulative parallel decode,
topology refinement, periodic checkpoint serialization, final PLY export,
evaluation, training and wall time. This prevents I/O tuning from being
credited with compute improvements and keeps performance A/B runs auditable.
It also records the decoded-image working-set size independently of the cache
limit, so a deployment can choose a host-RAM envelope from evidence rather
than from image count alone. When the working set exceeds the configured
ceiling and host-memory headroom is available, a cache ceiling rounded above
that working set is the first lossless performance candidate. Promotion still
requires an exact same-binary final-PLY comparison; container memory limits
must include the larger cache plus decoder and orchestration overhead.

`--optimizer-profile dronegs-dev16` is the deprecated compatibility default of
the standalone CLI. `--optimizer-profile reference-absolute` is the validated
production optimizer selected explicitly by DroneAI.
`reference-absolute-absgrad025` and
`reference-absolute-absgrad050` preserve that optimizer's rates and schedules
and add only a 0.25 or 0.50 robust absolute projected-gradient contribution to
MRNF growth ranking. They are qualification-only experimental candidates. The
`reference-dc-only`, `reference-position-only`, `reference-opacity-only`,
`reference-scale-only`, and `reference-rotation-only` values change exactly
one family for reproducible ablation. `reference-dc-opacity` combines only the
reference DC and opacity behaviors; position, scale, and rotation remain
exactly dev16.
`calibrated-dc-0.005-opacity`, `calibrated-dc-0.010-opacity`, and
`calibrated-dc-0.020-opacity` keep the LichtFeld opacity behavior and use the
named intermediate DC rate; all other parameter families remain exactly
dev16. Their historical two-scene results remain benchmark evidence, not the
current production selection. DroneAI's production pipeline overrides the
native default with the immutable dev.45-derived V1 recipe.

The output directory must be empty and must not contain the source dataset.
