# DroneGS changelog

This changelog covers the standalone Gaussian trainer project.

## 0.5.0-dev.72 - Reusable refinement host workspace

- Retain snapshot and five statistic buffers in each native training context;
  allocate only when the observed population exceeds the previous high-water mark.
- Initialize new storage only on first use/growth; complete every download
  before reading bounded active spans, excluding stale tails after compaction.
  Preserve the reference's six separate vectors; aggregated-storage experiments
  regressed fresh contexts and are not retained.
- Preserve the exact snapshot layout, median/pruning/scoring algorithms, CUDA
  work and checkpoint state. Retain 52 bytes per peak input Gaussian on the host.
- Add CPU lifetime/bounds/sentinel contracts and fresh-versus-reused full-state
  CUDA tests over changing populations, bounded/FastGS and opacity SH off/on.
- Extend the optional checkpoint benchmark with `fresh|reuse` context modes;
  always reload the frozen input and save first/last outputs in reuse mode.
- Qualify 13.31% shorter reused-context refinement on the frozen 5M checkpoint;
  fresh-context timing remains within the reference range. Reject two combined
  allocation variants that regressed fresh contexts. See
  [the qualification and RAM tradeoff](REUSABLE_REFINEMENT_HOST_DEV72.md).

## 0.5.0-dev.71 - Bounded CPU pruning percentiles

- Schedule the three independent spatial percentile pairs on at most three
  extra CPU threads while the caller computes the scale percentile. Preserve
  the exact existing floor-rank algorithms and subsequent pruning math.
- Keep populations/axes below 262,144 values sequential, as well as machines
  reporting fewer than four hardware threads; allow deferred runtime fallback.
- Keep all CUDA work on the caller and retain source-order scales for pruning.
- Add CPU bitwise/sorted oracles around the scheduling threshold and an optional
  alternating-order CPU benchmark; no profile, telemetry or checkpoint change.
- Qualify 32.13% shorter fenced refinement on the frozen 5M Saint-Etienne
  checkpoint versus dev.70, with exact real/synthetic full-state parity.
  CPU sanitizers, two-GPU native tests and portable compilation pass. See
  [the protocol, resource tradeoff and limits](PARALLEL_PERCENTILES_DEV71.md).

## 0.5.0-dev.70 - Compact pruning snapshot

- Download only a 32-byte transient pruning record per Gaussian instead of
  the full 296-byte AoS. Preserve position, log-scale and opacity bits; classify
  all opacity-SH coefficients with exact integer exponent checks on device.
- Keep percentile, scale, opacity, spatial and survivor decisions on the CPU.
  Borrow existing gradient scratch, processing at most 16 MiB per chunk.
- Reduce snapshot payload from 316 to 52 bytes per input including statistics;
  preserve Gaussian/PLY/checkpoint layouts and all training/rendering policies.
- Add bitwise projection and finiteness CPU oracles, exceptional values in
  every opacity-SH lane, chunk/guard/source-preservation and transfer contracts.
- Qualify byte-identical real/synthetic full checkpoints, 62.90% lower isolated
  refinement time versus dev.69 on the 5M Saint-Etienne state, Ampere/Ada native
  suites, Ada memcheck and portable compilation. See
  [measurements, provenance and limits](PRUNING_SNAPSHOT_DEV70.md).

## 0.5.0-dev.69 - Device-side Gaussian compaction

- Extend stable GPU compaction to Gaussian records, avoiding a second full
  host Gaussian vector and its upload; reuse existing bounded scratch storage.
- Compact the five CPU scoring-statistic vectors in place in survivor order.
- Reduce hard-compaction upload to survivor indices only, four bytes per row;
  preserve snapshot traffic, pruning, selection, split/decay and checkpoint ABI.
- Extend the bytewise CPU oracle to complete Gaussian records, including the
  internal chunk cap and untouched tails, and enforce the reduced upload contract.
- Qualify byte-identical full checkpoints on real 5M Saint-Etienne state and
  synthetic growth/recycling; isolated refinement falls 45.57% versus dev.68.
  Ampere/Ada suites, Ada memcheck and portable compilation pass. See
  [measurements and limitations](GPU_GAUSSIAN_COMPACTION_DEV69.md).

## 0.5.0-dev.68 - Device-side optimizer-state compaction

- Gather stable survivors directly on device for scalar, color-SH and optional
  opacity-SH Adam moments and refinement statistics, replacing host round trips.
- Borrow existing SH-gradient storage with a 16 MiB working-set cap; ordered
  chunks avoid in-place overwrite races without adding device allocations.
- Preserve CPU pruning, score/selection policy, Gaussian layout and full-state
  checkpoints. Keep hard compaction separate from existing in-place recycling.
- Add bitwise CPU-oracle tests for scalar/float2 fields, overlap, tails, bounds,
  signed zeros/NaN payloads and chunk limits; test bounded/FastGS resume with
  opacity SH both on and off.
- Qualify exact full-checkpoint parity on 5M Saint-Etienne Gaussians and a
  70.07% reduction in isolated refinement time on RTX 3090. Native tests pass
  on Ampere/Ada, memcheck reports zero errors, and portable compilation passes.
  See [the benchmark scope and retained evidence](GPU_COMPACTION_DEV68.md).

## 0.5.0-dev.67 - Refinement diagnostics

- Separate host allocation, snapshot download, pruning, moment compaction,
  scoring, top-K preparation, index upload and device submission durations.
- Record logical transfer bytes in refinement events and invocation-local
  manifest aggregates; leave checkpoint state and training policy unchanged.
- Add an optional CUDA benchmark using one frozen checkpoint per case and
  alternating instrumented/uninstrumented runs, plus CPU/CUDA/JSON contracts.
- Preserve exact Gaussian parity in the qualified synthetic cases. Timing on
  a shared GPU is noisy; no precise overhead bound or training speedup is claimed.
- See [measurement semantics and retained evidence](REFINEMENT_TELEMETRY.md).

## 0.5.0-dev.66 - Conditional opacity-SH storage

- Avoid inactive opacity-SH device gradients and Adam moments: 180 bytes per
  allocated Gaussian slot. Preserve Gaussian ABI, checkpoint layouts and resume.
- Pass the native CPU/CUDA allocation, topology and checkpoint regression gates.

## 0.5.0-dev.65 - Explicit directional opacity

- Make opacity-SH opt-in for custom runs; Production V1 uses scalar opacity
  while preserving color SH. Keep directional opacity available explicitly.

## 0.5.0-dev.64 - Multiwarp FastGS backward blocks

- Keep one 32-thread NVIDIA warp responsible for one FastGS backward bucket
  while scheduling four independent buckets per CUDA block with disjoint
  shared checkpoint state and portable CUDA warp primitives.
- Raise the RTX 3090 kernel's theoretical resident occupancy from 16 to 24
  warps per SM without changing bucket traversal, derivative equations or
  persistent VRAM. Performance remains qualified per GPU architecture.
- Pass all eight native CPU/CUDA CTests on RTX 3090.
- Pass all eight native CPU/CUDA CTests on RTX 4070 Laptop and build the
  portable CUDA target set from Turing through Blackwell. Performance remains
  measured only on RTX 3090.
- Repeat the fixed-topology 5.1 M-Gaussian gate twice: sampled iteration-999
  raster backward falls from 8.36/8.28 to 7.27/7.33 ms, mean native training
  falls from 34.912 to 34.028 seconds (`-2.53%`) and mean wall time falls from
  55.602 to 54.545 seconds (`-1.90%`), with equivalent loss, PSNR, SSIM and
  population.
- Complete two exact-commit 30,000-step HQ runs at 5.1 M Gaussians: mean
  native training falls `1.34%`, mean wall time falls `1.32%`, mean PSNR and
  pixel-weighted SSIM improve, and the repeated mean SSIM delta is
  `-0.001618`. The existing `0.002` quality threshold remains unchanged.

## 0.5.0-dev.63 - Interleaved SH Adam moments

- Specialize the coefficient-parallel SH Adam kernel for the three supported
  active SH layouts: 3, 8 and 15 coefficients per channel.
- Store each SH parameter's first and second Adam moments as one `float2`,
  reducing two scalar loads/stores to one vector load/store without changing
  the number of bytes, update equation or parameter order.
- Preserve checkpoint format v5. Capture interleaved moments directly, emit
  the legacy first-then-second layout in bounded chunks, and stream legacy
  reloads with one full scalar moment plus bounded conversion buffers.
- Pass all eight native CPU/CUDA CTests on RTX 3090 and parse the retained
  5.1 M-Gaussian dev.62 HQ checkpoint through checksum and optimizer-state
  validation.
- Repeat the fixed-topology 5.1 M-Gaussian gate twice: mean native training
  falls from 35.769 to 34.203 seconds (`-4.38%`) and sampled SH Adam from
  about 12.49 to 10.98 ms, with equivalent loss, PSNR, SSIM and population.
- Pass the exact-commit 30,000-step 5.1 M-Gaussian HQ gate: native training
  falls from 884.450 to 860.926 seconds (`-2.66%`) and wall time from 896.056
  to 871.779 seconds (`-2.71%`). Mean PSNR and SSIM improve while
  pixel-weighted SSIM changes by only `-0.000162`.

## 0.5.0-dev.62 - Subwarp scalar Adam

- Lay out each Gaussian's 14 scalar Adam parameters in a padded 16-lane
  subgroup, with two independent Gaussian groups per 32-lane warp.
- Normalize the four updated quaternion components through subgroup shuffles
  before the scalar optimizer kernel returns, removing the following
  full-model normalization kernel and memory pass.
- Preserve component-parallel update ordering, finite-value guards, identity
  fallback and the existing telemetry implementation.
- Pass all eight native CPU/CUDA CTests on RTX 3090.
- Repeat the fixed-topology 5.1 M-Gaussian gate twice: mean training falls
  from 37.561 to 35.769 seconds (`-4.77%`) and wall time from 58.526 to
  55.888 seconds (`-4.51%`), with equivalent loss, PSNR, SSIM and population.
- Complete the exact-commit 30,000-step HQ gate at 5.1 M Gaussians with three
  checkpoints: native training improves `3.71%`, wall time `3.66%`, and held-
  out PSNR/SSIM remain inside the established non-regression envelope.

## 0.5.0-dev.61 - Conservative projection culling

- Compute Gaussian scales before SH evaluation and reuse them in the exact
  covariance projection.
- Reject only splats whose opacity-one, maximum-axis support bound is entirely
  outside the image, with an outward rounding margin; keep exact projection as
  the authority for every uncertain case.
- Keep the prefilter stateless so geometry, rotation and scale updates require
  no visibility-cache invalidation protocol.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including an explicit
  off-image-center/overlapping-support parity regression.
- Repeat the fixed-topology 5.1 M-Gaussian gate twice: mean training falls
  from 39.034 to 37.561 seconds (`-3.77%`) with equivalent loss, PSNR, SSIM
  and Gaussian population.
- Complete the exact-commit 30,000-step HQ gate at 5.1 M Gaussians with three
  checkpoints: native training improves `2.49%`, wall time `2.44%`, and held-
  out PSNR/SSIM remain inside the established non-regression envelope.

## 0.5.0-dev.60 - Fused FastGS SH Adam

- Consume raw FastGS RGB/opacity derivatives directly in scalar and SH Adam,
  applying DC scaling and SH basis products at the final point of use.
- Avoid materializing and clearing expanded color-SH and opacity-SH gradient
  buffers in structural FastGS training while preserving zero-gradient Adam
  moment decay for invisible Gaussians.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including active SH3 and
  invisible-appearance regression coverage.
- Repeat the fixed-topology 5.1 M-Gaussian gate twice: mean training improves
  another `7.74%` from dev.59 and `16.68%` from dev.58. Aggregate wall improves
  `4.07%` from dev.59 and `10.93%` from dev.58 with equivalent quality.

## 0.5.0-dev.59 - Active-only FastGS SH expansion

- Accumulate raw RGB and opacity derivatives once per source/tile in the
  structural FastGS backward pass, then expand DC, color-SH and opacity-SH
  gradients once per active Gaussian.
- Skip the expansion for exact-zero appearance gradients, avoiding writes for
  invisible Gaussians while preserving all geometry/refinement derivatives.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including an active-SH
  update and invisible-appearance regression.
- Repeat the fixed-topology 5.1 M-Gaussian cell twice: mean training falls
  from 46.848 to 42.311 seconds (`-9.69%`), aggregate wall from 134.308 to
  124.701 seconds (`-7.15%`) and sampled late raster backward from 14.664 to
  9.485 ms (`-35.3%`) with equivalent loss, PSNR and SSIM.

## 0.5.0-dev.58 - Bounded asynchronous checkpoints

- Capture a complete immutable GPU training snapshot before resuming updates,
  then checksum, sync and atomically publish it on one background writer.
- Keep at most one checkpoint in flight and propagate delayed write failures
  before the next snapshot or trainer completion.
- Report snapshot stall, completion wait, background write time and checkpoint
  count separately in the run manifest.
- Bound standard 7,500/15,000/30,000 iteration runs to one/two/three recovery
  checkpoints while retaining zero as the explicit disable value.

## 0.5.0-dev.57 - Refinement-statistics cooldown

- Stop generating refinement-only SSIM error/Sobel maps, frame weights,
  visibility/edge/AbsGrad accumulators and their persistent reduction once no
  later scheduled topology refinement can consume them. Image gradients,
  geometry gradients and all optimizer updates remain unchanged.
- Keep objective-only evaluation free of persistent refinement-statistic side
  effects and expose an explicit collect/skip contract at the ordered trainer
  boundary.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including schedule and
  one-step collect/skip model-parity regressions.
- Repeat a 100-step fixed-topology micro-benchmark twice: mean training time
  falls from 0.3990 to 0.3417 seconds (`-14.4%`) with equivalent model and
  held-out metrics. Two complete GAJAN runs per profile improve Fast training
  by `0.6%` and Normal training by `0.2%`; held-out PSNR/SSIM and Gaussian
  population remain inside retained run variation.

## 0.5.0-dev.56 - Tile-local objective reduction

- Replace per-active-pixel global L1-loss and active-count atomics in the
  fused L1/SSIM forward pass with a shared-memory 16x16 tile reduction and one
  pair of global atomics per non-empty tile. Objective equations, SSIM terms,
  image gradients and optimizer behavior are unchanged.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including the existing
  fused-objective reference and finite-difference gradient checks plus a new
  non-multiple-of-16 partial-tile objective regression.
- Repeat GAJAN Fast and Normal twice. Fast mean wall improves from 26.337 to
  20.896 seconds (`-20.7%`) and training from 22.966 to 17.337 seconds
  (`-24.5%`). Normal mean wall improves from 71.779 to 58.948 seconds
  (`-17.9%`) and training from 68.203 to 55.307 seconds (`-18.9%`). Held-out
  PSNR/SSIM remain inside retained variation and the Normal Gaussian population
  is unchanged at 191,547 mean.

## 0.5.0-dev.55 - Component-parallel scalar Adam

- Extend sampled GPU telemetry with objective-gradient, gradient-reset,
  raster-backward, geometry-backward, scalar-Adam, SH-Adam and optimizer-post
  timings while preserving existing aggregate fields.
- Update the 14 scalar parameters of each Gaussian with independent CUDA work
  items and normalize quaternions in a following ordered kernel. Preserve the
  original equations, moments, schedules, epsilon values, clamps and sampled
  optimizer-telemetry path.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including a forced-path
  finite-parameter and unit-quaternion invariant test.
- Repeat GAJAN Fast and Normal twice. Normal mean wall improves from 76.834 to
  71.779 seconds (`-6.6%`) and training from 73.306 to 68.203 seconds
  (`-7.0%`), with quality and topology inside retained variation and VRAM near
  5.0 GiB.

## 0.5.0-dev.54 - Bounded tile/depth radix range

- Limit the persistent tile/depth radix sort to all 32 depth bits plus the
  active tile-identifier bits, excluding only constant zero high bits.
- Validate the tile count before deriving the CUB radix range; key layout,
  stable pair ordering and renderer inputs remain unchanged.
- Pass all eight native CPU/CUDA CTests on RTX 3090. Two GAJAN Fast runs
  improve mean wall time from 27.080 to 26.554 seconds (`-1.9%`) and training
  from 23.814 to 23.140 seconds (`-2.8%`). The pair-sort stage improves by
  `21.5%`; held-out quality and topology remain inside run variation.
- Repeat GAJAN Normal twice at 76.834 seconds mean wall and 73.306 seconds
  mean training, `9.0%` and `9.5%` faster than dev.52. Mean held-out quality
  remains inside the dev.50–dev.52 envelope and observed VRAM stays near
  5.0 GiB.

## 0.5.0-dev.53 - Direct tile/depth ordering

- Decompose sampled preprocessing telemetry into projection, projected-record
  sort, binning/duplication, tile/depth pair sort and FastGS bucket timings,
  while preserving the aggregate `preprocess_ms` field.
- Remove the redundant global projected-record radix sort from persistent
  training. The required `(tile, depth)` radix sort and original Gaussian
  source indices remain unchanged.
- Remove the two persistent output buffers formerly required by that sort,
  saving 60 bytes of capacity per Gaussian without changing the public
  checkpoint or PLY formats.
- Pass all eight native CPU/CUDA CTests on RTX 3090. Two GAJAN Fast runs
  improve mean wall time from 29.334 to 27.080 seconds (`-7.7%`) and training
  from 26.077 to 23.814 seconds (`-8.7%`), with PSNR/SSIM and final population
  inside retained dev.52 variation.

## 0.5.0-dev.52 - Coefficient-parallel SH Adam

- Move active color-SH and opacity-SH Adam updates from serial per-Gaussian
  loops to a coefficient-parallel CUDA kernel. DC, scalar opacity, position,
  scale, rotation and sampled optimizer telemetry remain unchanged.
- Preserve the exact moment equations, bias correction, learning rates,
  epsilon values and progressive-SH activation boundary without adding a
  persistent device allocation.
- Pass all eight native CPU/CUDA CTests on RTX 3090, including checkpoint,
  deferred/synchronous, SH, opacity-SH and Python/native parity canaries.
- Repeat GAJAN Fast twice at 29.333 seconds median versus 38.985 seconds for
  dev.51 (`-24.8%` wall; `-26.9%` training). Mean PSNR, pixel-weighted PSNR
  and SSIM remain inside reference run variation.
- Complete GAJAN Normal 15k in 84.472 seconds versus 162.594 seconds in the
  retained reference manifest (`-48.0%` wall; `-49.0%` training), while mean
  PSNR improves 18.9780 → 18.9865 dB, pixel-weighted PSNR 16.4689 → 16.4947
  dB and SSIM 0.373993 → 0.374458. Observed VRAM remains below 5.2 GiB.

## 0.5.0-dev.51 - Sampled GPU stage telemetry

- Time preprocessing, rasterization, L1/DSSIM objective, backward and Adam
  with CUDA events at six staggered training steps. The stagger avoids the
  existing optimizer-statistics samples and adds no per-iteration readback.
- Emit one machine-readable `gpu_stage_telemetry` JSON event per sample with
  all five stage durations and their total.
- Qualify the instrumentation on GAJAN Fast, RTX 3090: 38.985 seconds wall,
  16.8101 dB mean PSNR, 14.4438 dB pixel-weighted PSNR, 0.311236 SSIM and
  54,881 final Gaussians. These match the retained Fast reference within run
  noise.
- Identify late SH3 Adam (34–46%) and projection/sort/binning (22–30%) as the
  dominant sampled GPU costs. Rasterization is only 1–4%, so further speed
  work must target optimizer parallelism and preprocessing rather than loosen
  rendering quality.
- Fix benchmark output ownership by creating bind-mount sources before Docker
  starts, and accept nested native PLY output paths.
- Reject and remove the FastGS-style view-consistent density prototype after
  controlled Fast and Normal regressions; retain its negative qualification
  report without exposing a dead production option.

## 0.5.0-dev.50 - Deferred metrics and exact bounded tile culling

- Keep L1, active-pixel and SSIM scalars on the GPU for non-reporting training
  iterations, removing three synchronous device-to-host copies from the hot
  path while retaining 20 evenly spaced progress samples plus first/final.
- Validate empty frames and non-finite loss/SSIM values on device through a
  sticky error flag, then fail closed at the next progress readback or final
  iteration.
- Add conservative projected-ellipse/tile intersection to the bounded CUDA
  renderer. Tests require identical visible/contributing pairs and no increase
  in evaluated candidates against the CPU oracle.
- Preserve the structural FastGS candidate stream because precise culling
  changes packed checkpoint groups; its separate compatibility tests and
  checkpoint semantics remain unchanged.
- Pass all eight native CUDA/CPU CTests on the RTX 3090 target. A fixed-topology
  real-cell pilot measured 1.011 seconds versus 1.060 seconds of training
  (`-4.7%`), while the roughly 183-second pilot wall time remained dominated
  by one-time projected initialization and image loading. Treat that short
  measurement as directional pending the longer GAJAN qualification.

## 0.5.0-dev.49 - Projected initialization and run-scaled capacity

- Add crop-camera projected KNN initialization with a configurable maximum
  screen-space sigma while preserving local KNN for immutable profiles.
- Bind initialization policy, projected ceiling and maximum scale growth to
  CLI, checkpoint identity, manifests, workers, API and Dashboard controls.
- Extend checkpoint V5 metadata to preserve initial pixel-weighted PSNR/SSIM
  across resume while retaining V4 read compatibility.
- Derive topology growth, pruning and position-noise boundaries from every
  operator-selected iteration budget: 3,600 for Fast 7,500, 7,400 for Normal
  15,000 and 14,800 for HQ 30,000, with the same formula for manual budgets.
- Let capacity-targeted profiles request a bounded 7–50% split population and
  delegate the last growth window to the deterministic hard cap, eliminating
  final estimator drift without exceeding the operator ceiling.
- Add opt-in Fast-v2, Normal-v4 and High-Quality-v4 profiles without changing
  the qualified Normal-v3 default or historical stored profile identities.
- Verify projected parameters through map and facade process contracts and
  expose the candidates in the Dashboard only under the existing strict
  qualification flag.
- Qualify Fast-v2 on a track-authoritative Silo cell at 1.5 M Gaussians and
  exercise Normal-v4 checkpoint/resume at exactly 3 M within the 8 GiB VRAM
  envelope. Full HQ, multi-block products and facade remain separate gates.

## 0.5.0-dev.48 - Native image tiles and opacity-SH v1

- Make tile modes operational: mode 2 splits the longest image axis and mode
  4 trains on a 2-by-2 crop grid with crop-relative principal points.
- Apply the width ceiling per crop, retain all source pixels when a crop fits,
  and replace nearest-neighbour resize with area resampling. Fractional source
  coverage is integrated explicitly so high-frequency image energy is not
  aliased into the training target.
- Split datasets by source photograph before expanding tiles, preventing
  train/held-out leakage while evaluating every held-out crop.
- Add directional opacity-logit SH coefficients through CPU/CUDA forward,
  analytical backward, Adam, topology split/compaction, PLY and checkpoint
  persistence. This capability is named `opacity-SH-v1`; scale and rotation
  remain view-independent.
- Align the CuPy orthomosaic SH signs with the native DroneGS basis and consume
  `opacity_sh_*` properties during nadir rendering.
- Centralize the Python/CuPy SH basis and compare it directly with a native
  C++ probe for normalized degree-0 through degree-3 directions in CTest.
- Move checkpoints to format V4 because the Gaussian and optimizer state now
  contains opacity-SH arrays; older checkpoints fail closed and must restart
  training from their source dataset or final PLY.
- Add focused CPU/CUDA parity, four-crop training, opacity-SH learning and
  checkpoint round-trip canaries.
- Rename the Python boundary from ambiguous `fagk` flags to
  `opacity_sh_enabled`, and document that scale/rotation remain static.
- Refuse adaptive Normal/HQ rasterization when the post-filter Gaussian count
  cannot support the requested GSD at the profile's declared spacing; persist
  the capacity plan and density verdict through Stage Job artifacts.
- Define partition cores/buffers in projected ground coordinates, assign
  cameras through calibrated terrain-envelope footprint overlap, and compose
  lossless native JPEG block crops with `tile_mode`. Dataset fingerprints v3
  include the crop contract.

## 0.5.0-dev.47 - Production identity and spatial canary

- Add a versioned native profile registry and reject unknown or
  non-production profile/configuration combinations.
- Extend the dataset identity to cameras, intrinsics, image IDs, poses, sparse
  points and stable image samples; completed reuse also verifies trainer and
  PLY SHA-256.
- Add deterministic `spatial-block` held-out selection and an optional guard
  ring while preserving modulo-8 parity in immutable production V1.
- Record training, held-out and ignored camera counts in the manifest and
  canary result.
- Upgrade checkpoints to format V3 with a payload checksum, fixed-width new
  fields, file and parent-directory fsync, and rollback-safe publication while
  retaining V1/V2 read compatibility.
- Keep recovery checkpoints outside disposable mission workspaces, synchronize
  every save to S3, restore on pod replacement and retire only after final
  artifact promotion.
- Make cancellation poll independently of trainer stdout and terminate the
  complete process group.
- Harden the benchmark harness with isolated empty trainer outputs,
  driver/CUDA/thermal/VRAM inventory, five-run dispersion and portable
  archives.
- Validate five complete SAVERES production V1 seeds with 607.1-second median
  wall time, 2,124 MiB median peak VRAM, 19.4122 dB mean PSNR and 0.49155 mean
  SSIM over 134 held-out views.
- Make DroneGS the sole distributed and local Gaussian training backend.
- Remove the LichtFeld checkout, patches, Dockerfile, Python adapter, runtime
  selector and vcpkg build dependency while retaining historical benchmark
  and GPL provenance documentation.
- Add atomic, versioned full-state checkpoints, strict dataset/config
  fingerprints, deterministic resume, deliberate checkpoint canaries and
  held-out PSNR/SSIM deployment gates.
- Freeze the mission and balanced-local production recipe at 15,000 steps,
  SH3, factor 4, width 1,600, 1.5 million splats, seed 42, structural FastGS,
  bounded spatial pruning, 1,000-step topology cooldown and
  1,000-step 100%-MSE photometric finish.
- Pass every native tuning control through the backend-neutral Python
  contract, API mission parameters, dashboard controls and local runners.
- Build portable Turing-through-Blackwell DroneGS device code in the default
  COLMAP and local Gaussian images. Ship the matching source tree and GPL
  provenance register beside the native binary.
- Record the final Albagnac parity gate: 22.175919 dB PSNR, 0.642557 SSIM,
  0.325408 LPIPS and 972.731 training seconds versus deterministic
  LichtFeld's 21.513821 dB, 0.586497, 0.371055 and 994.228 seconds.
- Pass the independent Savères 15,000-step production canary on 1,065
  images: 19.163038 dB PSNR, 0.456047 SSIM, 0.551232 LPIPS and 2,455.774
  training seconds. Delete the 1.1 GiB optimizer checkpoint only after all
  final artifacts and gates are published.
- Validate a real stop-at-100/resume-to-200 integration canary against a
  continuous 200-step control within 0.000126 dB PSNR and 0.000017 SSIM.
- Preserve the original initial loss and held-out metrics in checkpoint
  format v2 while retaining read compatibility with format v1.
- Keep the native command-line defaults backward compatible; the production
  profile is applied by the DroneAI orchestration boundary.

## 0.5.0-dev.45 - Progressive photometric finish

- Add opt-in `--photometric-finish N` and
  `--photometric-mse-percent P`.
- Linearly blend the final `N` training steps from the established
  `0.8 active-pixel L1 + 0.2 DSSIM` objective toward active-pixel MSE,
  reaching `P%` MSE weight on the final step.
- Fuse the analytical MSE derivative into both structural FastGS and bounded
  backwards. The training hot path skips the unused scalar MSE reduction;
  exact mixed values remain available to evaluation and gradient tests.
- Keep per-step loss telemetry on the baseline L1+DSSIM objective for
  comparable convergence curves while applying the mixed analytical
  gradient to optimizer updates.
- Keep both controls at zero by default so dev.44 loss, gradients, and
  execution cost remain unchanged.
- Record the exact schedule in the run manifest and progress event stream.

## 0.5.0-dev.44 - Fixed-topology convergence cooldown

- Add an opt-in `--topology-cooldown N` within the existing iteration budget.
- Stop prune/grow/recycle refinement after `iterations - N` and use the final
  `N` steps for optimizer-only convergence.
- Keep the default at zero so existing contracts retain their exact topology
  lifecycle until an ablation demonstrates a quality benefit.
- Record the configured cooldown and effective last refinement iteration in
  the run manifest.

## 0.5.0-dev.43 - Bounded scene-resident RGB8 cache

- Size the decoded RGB8 image cache from the complete resized COLMAP scene
  instead of fixing it at 256 MiB.
- Keep a strict 2 GiB ceiling and a 256 MiB floor, preventing the unbounded
  host-memory behavior previously observed outside DroneGS.
- Preserve the deterministic camera schedule, JPEG decoder, image bytes,
  prefetch semantics, CUDA uploads, and training math.

## 0.5.0-dev.42 - Structural FastGS backend

- Replace the FastGS-math compatibility emulation with scanned 32-instance
  buckets, a persistent bucket-to-tile map, packed RGBA8 checkpoints, last
  contributor counters, and per-tile contribution bounds.
- Add a one-warp-per-bucket backward traversal. Each lane owns one projected
  Gaussian, receives pixel state diagonally through warp shuffles, accumulates
  gradients in registers, and emits one atomic series per bucket instance.
- Fuse active-pixel L1 and valid-window SSIM forward into one 16x16
  shared-memory kernel, then fuse their analytical image gradients into a
  second tiled kernel.
- Keep COLMAP/image loading, split/sampler, MRNF topology lifecycle, optimizer,
  CLI, manifests, and job orchestration independent of LichtFeld.
- On the strict Albagnac 1,000-step pilot, reduce training time from
  `30.152 s` to `20.290 s` (`-32.7%`) while changing held-out quality by
  `+0.00700 dB PSNR` and `+0.000055 SSIM`.

## 0.5.0-dev.41 - Cached forward state for backward

- Persist each pixel's final transmittance and active pair-list endpoint
  during the forward raster pass.
- Remove the redundant front-to-back replay previously performed by the
  backward raster kernel.
- Start each tile's reverse traversal at the maximum active endpoint across
  its pixels, skipping trailing pair batches that could not contribute.
- Preserve the exact blending order, alpha cutoff, gradients, and
  architecture-independent CUDA path.

## 0.5.0-dev.40 - Raster decoupling and GPU slot recycling

- Add `--raster-profile auto|bounded|fastgs` so optimizer rates no longer
  select rendering semantics implicitly.
- Enable the strict parity combination: LichtFeld absolute optimizer rates,
  LichtFeld pruning bounds, and the FastGS rasterizer.
- At a full Gaussian cap, replace pruned splats directly in their GPU slots
  when the growth budget covers them, avoiding full host compaction of every
  Gaussian and every Adam moment.
- Keep the historical compaction path as a fallback when growth cannot fill
  all holes, including the terminal prune-only refinement.

## 0.5.0-dev.39 - Controlled pruning and exact-parity audit

- Add `--pruning-policy original|lichtfeld-bounds`.
- Reproduce LichtFeld's central-80% `100 * max_extent` spatial and scale
  pruning bounds without changing the default policy.
- Emit non-finite, opacity, small-scale, large-scale, and spatial prune
  counters at every topology refinement.
- Add an opt-in GPL-covered LichtFeld sampler patch controlled by
  `LFS_SAMPLER_SEED` for deterministic cross-engine camera replay,
  including ordered delivery from the asynchronous image pipeline and
  stable MRNF selection/noise seeds.
- Correct the Albagnac parity protocol: dev.38 and LichtFeld shared the
  dataset, split, budget and topology cap, but not every optimizer rate.

## 0.5.0-dev.38 - Coupled FastGS compatibility and quality parity

- Add a gated FastGS-compatible raster profile that jointly implements
  `0.3 I` projected covariance dilation, extended-FOV Jacobian clamping,
  opacity-dependent support at the `1/255` alpha threshold, a `0.999`
  fragment-alpha ceiling, and the corresponding analytical backward path.
- Add a strict binary little-endian Gaussian PLY reader and `--initial-ply`
  option so renderer and learned-parameter differences can be measured
  independently on the exact same camera split.
- Recover `+3.6785 dB` by rendering the pinned LichtFeld PLY through the
  coherent dev.38 profile instead of DroneGS's historical bounded renderer.
- Reach `19.15709 dB / 0.440746 SSIM` after 1,200 Albagnac steps, exceeding
  the exact LichtFeld PLY oracle on the identical 172-view split
  (`18.90036 dB / 0.428674`) by `+0.25673 dB / +0.012072`.
- Improve GAJAN over dev.36 by `+1.73765 dB / +0.022099 SSIM` and Savères by
  `+0.03032 dB / +0.002640`, using only existing COLMAP outputs.
- Record the FastGS adaptation in the GPL provenance register. The new profile
  is architecture-independent and retains automatic recent-NVIDIA CUDA
  selection.

## 0.5.0-dev.37 - Compensated anti-aliasing ablation

- Add opt-in screen-space covariance filters of `0.05`, `0.15`, and `0.30`
  pixel squared on top of dev.36 AbsGrad `0.50`.
- Preserve projected Gaussian energy with
  `sqrt(det(covariance) / det(filtered_covariance))`.
- Propagate the compensation derivative analytically through covariance,
  scale, rotation, and position instead of applying a forward-only filter.
- Keep the classic path at zero filter variance and architecture-independent.
- Classify the profiles as metric-specific rather than balanced: they improve
  Albagnac PSNR/SSIM, but all tested strengths regress exact-pair LPIPS.

## 0.5.0-dev.36 - AbsGrad-guided MRNF

- Accumulate the absolute X/Y projected-center gradient contribution of every
  Gaussian/pixel pair before signed gradients can cancel, following the AbsGS
  mechanism.
- Average the resulting homodirectional norm per visible training view,
  normalize by its positive scene median, clamp outliers to four, and combine
  it with the existing MRNF error/edge score before deterministic Gumbel
  selection.
- Add isolated score weights `0.25` and `0.50` on top of the selected dev.35
  staged-rotation `0.008` profile.
- Pin the audited Apache-2.0 gsplat reference revision in the provenance
  register; no new runtime dependency or architecture-specific path is added.

## 0.5.0-dev.35 - Staged rotation calibration

- Add two architecture-independent profiles that keep the dev.34 scale
  schedule, use rotation LR `0.001` for the first 40% of optimizer steps, and
  then switch to `0.004` or `0.008`.
- Record the initial/final rotation rates and switch fraction in the run
  manifest so the schedule is fully reproducible.
- Select `0.008` over `0.004` on Albagnac, where it improves all three
  held-out metrics over dev.34.
- Keep dev.35 opt-in: it improves LPIPS over dev.34 on all three scenes but
  remains 0.12% behind the balanced dev.33 Savères LPIPS result and trades
  some GAJAN PSNR for perceptual quality.

## 0.5.0-dev.34 - Scale/rotation structure profiles

- Add isolated post-KNN scale, rotation, and combined profiles on top of the
  accepted dev.33 opacity calibration.
- Pass all six native/LPIPS suites and preserve automatic portable CUDA
  architecture selection.
- Improve Albagnac by 0.2326 dB / 0.01185 SSIM / 0.62% LPIPS and GAJAN by
  0.1544 dB / 0.00438 SSIM / 1.11% LPIPS with the combined profile.
- Improve Savères PSNR/SSIM by 0.0843 dB / 0.00367, but regress LPIPS by
  0.33%; retain the combined profile as opt-in and keep dev.33 as the balanced
  quality recommendation.

## 0.5.0-dev.33 - Post-KNN opacity convergence

- Add isolated `0.024`, `0.048`, and `0.096` opacity-rate profiles while
  retaining DC `0.010`, opacity epsilon `1e-15`, and unchanged dev.32
  position/scale/rotation behavior.
- Select `calibrated-dc-0.010-opacity-0.096` as the recommended quality
  profile after independent Albagnac, GAJAN, and Savères validation.
- Improve all three held-out metrics on every scene. Against dev.32, Albagnac
  gains 0.9791 dB / 0.01602 SSIM / 10.83% LPIPS; GAJAN gains
  0.4698 dB / 0.01225 / 3.17%; Savères gains
  0.1397 dB / 0.00365 / 7.53%.
- Raise median final opacity to 0.400 on Albagnac and 0.428 on Savères,
  approaching the pinned LichtFeld opacity regime while preserving the same
  `0.1` initialization and bounded covariance renderer.
- Keep the CLI default unchanged while Phase 4 remains experimental; retain
  all sweep points for reproducibility.

## 0.5.0-dev.32 - Extended live SH color

- Match pinned LichtFeld FastGS by clamping per-splat SH color to `[0,4]`
  instead of `[0,1]`.
- Keep DC and active higher-order SH gradients live over the same extended
  interval in both the CPU oracle and CUDA backward path.
- Add explicit CPU ceiling/live-gradient checks and exercise extended color in
  CUDA forward/backward parity.
- Mark the adapted CPU rasterization translation unit GPL-3.0-or-later and
  extend the exact-source provenance record.

## 0.5.0-dev.31 - Local-density MRNF initialization

- Replace the single scene-wide Gaussian scale with the MRNF two-nearest-
  neighbour scale formula adapted from pinned LichtFeld.
- Add an independent deterministic balanced KD tree and parallel exact queries
  without adding a runtime library dependency.
- Bound local scales by central-75% scene extents and preserve LichtFeld's
  `1e-3` scale floor and fewer-than-three-points fallback.
- Mark the adapted initialization translation unit GPL-3.0-or-later and extend
  the provenance register with its exact source revision.
- Add compact/diffuse-neighbourhood, duplicate-point, isotropy, and fallback
  tests while retaining all six CPU, CUDA, training, and LPIPS-tool suites.

## 0.5.0-dev.30 - Portable recent-NVIDIA CUDA builds

- Remove the Ada-only CUB Policy610 override and `--maxrregcount=64` compiler
  ceiling while retaining dev.29's generic shared-memory backward batching.
- Default local builds to CMake's `native` CUDA architecture detection.
- Add a `portable` preset with CUDA 12.8 real cubins for Turing, Ampere,
  Ada, Hopper, and Blackwell (`75`, `80`, `86`, `87`, `89`, `90`, `100`,
  `101`, and `120`).
- Keep explicit user-provided CMake architecture lists supported and give
  direct `CMAKE_CUDA_ARCHITECTURES` settings precedence.
- Return both stable key/value sorts to CUB's public default dispatch so nvcc
  and CUB select a valid policy for each compiled target.

## 0.5.0-dev.29 - Phase 4 shared-memory backward batching

- Cooperatively load projected splats into tile-local shared memory during
  both front-to-back transmittance recomputation and reverse gradient
  traversal.
- Share the recovered source index alongside each 48-byte projected record,
  removing up to 256 redundant global record/key reads per tile contribution.
- Preserve the exact per-pixel blend order, reverse gradient order, stable
  depth/source key, public outputs, and MRNF lifecycle.
- Pass all six CPU, CUDA, gradient, training, and LPIPS-tool suites.
- Reduce bounded Savères training/wall time to 39.50/43.97 seconds and
  Albagnac to 50.44/56.21 seconds. This is about 30% and 33% faster in wall
  time than the dev.26 PTX reference.
- Preserve Savères and Albagnac topology exactly; bounded PSNR, SSIM, and
  exact-pair LPIPS remain within numerical noise of dev.26/dev.28.
- Keep DroneGS opt-in. Speed parity is reached on these bounded three-scene
  controls, but convergence-length LichtFeld, visual, orthomosaic, and
  downstream detection gates remain open.

## 0.5.0-dev.28 - Phase 4 native Ada radix and occupancy tuning

- Keep dev.27's deterministic 64-bit depth/source key and compact 48-byte
  projected record after rejecting a 32-bit-key/52-byte-record alternative on
  Savères.
- Override CUDA 12.8's slower native Ada radix selection with stable CUB
  Policy610 kernels for both projected-depth and tile/depth pair sorts.
- Cap CUDA registers at 64 only when architecture 89 is compiled. Reject 96
  registers as neutral and 48 registers because spilling regresses throughput.
- Preserve all six CPU, CUDA, training, and LPIPS-tool suites and the existing
  equal-depth stability contract.
- Improve bounded Savères training/wall time from 58.25/62.77 seconds in
  dev.27 to 55.77/61.56 seconds. Improve Albagnac from 82.99/88.96 to
  79.87/87.52 seconds, with identical final topology and negligible
  PSNR/SSIM deltas.
- Beat the dev.26 PTX baseline in Savères wall time, while Albagnac remains
  4.3% slower in wall time. Keep the broader Phase 4 speed-parity gate open.
- Use only existing read-only COLMAP dense outputs; do not rerun COLMAP or the
  combined approximately 2,000-photo Albagnac workload.

## 0.5.0-dev.27 - Phase 4 native sm_89 compact-record sort

- Establish `89-real;89-virtual` before CMake enables CUDA so a clean default
  build contains an actual `sm_89` cubin plus compute_89 PTX.
- Replace the 144-byte projected CUB value with a 48-byte render record;
  preserve the existing depth/source key and keep the 16 SH bases in a
  separate source-indexed buffer.
- Reconstruct tile bounds from the sorted projected center/radius in two
  coalesced kernels. Avoid a full-record gather and reduce persistent
  projected-depth capacity by 112 bytes per reserved Gaussian.
- Pass all six CPU, CUDA, training, and LPIPS-tool suites; `cuobjdump` confirms
  `dronegs.1.sm_89.cubin`.
- Re-run bounded MRNF/progressive-SH validation on GAJAN, Savères, and
  Albagnac using existing read-only COLMAP dense outputs. No reconstruction,
  bundle adjustment, or combined 2,000-photo throughput run is performed.
- Preserve large-scene topology and PLY byte size exactly. PSNR/SSIM/LPIPS
  deltas stay below 0.00005 dB / 0.000004 / 0.00007 on Savères and Albagnac.
- Improve GAJAN training/wall time by 5.8%/15.5% versus the dev.26 PTX-JIT
  binary. Savères is wall-neutral (+0.4%); Albagnac remains +6.0% wall and
  needs kernel-level profiling before any broad speed claim.
- Keep the native trainer opt-in and the Phase 4 production gate open.

## 0.5.0-dev.26 - Phase 4 three-scene MRNF/SH/LPIPS validation

- Validate the dev.25 trainer without rerunning COLMAP on the existing GAJAN
  smoke, Savères, and Albagnac reconstructions: 25, 1,065, and 1,376 images
  with 9,324, 642,161, and 1,025,093 initial Gaussians respectively.
- Complete 1,200 bounded iterations on GAJAN and 220 bounded iterations on
  both large scenes. All runs reach progressive SH degree 3 and exercise the
  complete MRNF prune/reuse/noise/decay/compaction path without CUDA OOM.
- Measure final held-out PSNR/SSIM of 14.13256/0.205984 on GAJAN,
  15.72116/0.110614 on Savères, and 16.86978/0.240971 on Albagnac.
- Compute exact-pair AlexNet LPIPS v0.1 means of 1.046475, 1.201670, and
  1.085614 on 5, 17, and 22 held-out views respectively.
- Record wall times of 16.79 s, 62.49 s, and 83.94 s for the bounded runs.
  These establish execution and scaling evidence, not converged quality,
  full-dataset throughput, or LichtFeld parity.
- Audit the actual CUDA image in the validated binary: the pre-existing CMake
  cache produced sm_52 plus PTX, not the intended native sm_89 image. CUDA
  12.8/CUB native sm_86 and sm_89 device links currently fail because
  `DeviceRadixSort` Policy900 emits 50.5--51.5 KiB of static shared data over
  the 48 KiB linker limit. Record native Ada radix-sort repair as an open gate.
- Preserve owner and permissions during atomic LPIPS result/manifest writes;
  new evaluator files use mode `0644` and inherit the evaluation directory
  owner even when the container runs as root.
- Correct the documented LPIPS CLI invocation and add a persistent named
  Docker volume for the separately tracked AlexNet weight cache.
- Keep the Phase 4 production gate open. No full Albagnac throughput run,
  COLMAP bundle adjustment, or LichtFeld oracle replay is part of this phase.

## 0.5.0-dev.25 - Phase 4 complete MRNF lifecycle

- Continue refinement every 200 steps through iteration 28,500 while stopping
  growth at 15,000; later windows remain useful for prune/decay/compaction.
- Prune raw opacity below `logit(1/255)`, scale below `1e-10`, non-finite
  parameters, excessive-scale outliers, and robust spatial outliers.
- Hard-compact survivors into a dense device prefix while preserving all DC,
  SH, opacity, position, scale, and rotation Adam moments.
- Split selected parents after compaction, report reused versus newly appended
  slots, and retain the existing deterministic weighted-Gumbel/edge guidance.
- Inject deterministic opacity-weighted Gaussian means noise with the pinned
  MRNF exponent 150 and weight 50, bounded by each Gaussian's median scale.
- Apply refinement-time opacity decay 0.004 and scale decay 0.002, attenuated
  by remaining training fraction.
- Extend progress events and run manifests with prune/reuse/compaction counts
  and all lifecycle constants.
- Add synthetic tests for prune/compact/reuse and seed-stable noise.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.24 - Phase 4 progressive spherical harmonics

- Extend every Gaussian with the standard 45 channel-major non-DC values used
  by degree-3 3DGS PLY files; exports now contain learned values, not zeros.
- Add matching real SH bases for degrees 0–3 to the CPU oracle and CUDA
  rasterizer, with view direction computed from the camera center.
- Back-propagate active non-DC color coefficients on CPU/CUDA and update them
  with persistent Adam moments at one twentieth of the active DC rate.
- Start at degree zero and activate one band every
  `--sh-degree-interval` steps (default 1,000), capped by `--sh-degree`;
  emit schedule events and record the configured/final degree in the manifest.
- Copy SH values during splits and reset parent/child SH Adam moments.
- Add finite-difference coefficient tests, CPU/CUDA degree-3 parity, schedule
  tests, CLI validation, and real non-DC PLY serialization.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.23 - Phase 4 exact-pair LPIPS evaluation

- Persist exact lossless RGB8 held-out targets beside final PPM predictions;
  filenames are identical and pairing fails closed on missing or extra views.
- Add a separate, reproducible LPIPS v0.1 evaluator using the official
  `lpips` package with AlexNet and the required `[-1, 1]` input range.
- Write per-view `evaluation/lpips.csv`, aggregate
  `evaluation/lpips.json`, and atomically enrich run-manifest-v1 with the
  score, network, evaluator version, view count, hashes, and artifact sizes.
- Isolate PyTorch, torchvision, LPIPS, Pillow, and model-weight acquisition in
  `Dockerfile.lpips`; the native CUDA trainer and its hot path are unchanged.
- Add dependency-free unit coverage for exact pairing, aggregation, percentile
  calculation, atomic manifest enrichment, and mismatch rejection.
- No COLMAP reconstruction or full Albagnac throughput run is part of this
  phase.

## 0.5.0-dev.22 - Phase 4 two-scene DC validation

- Prepared the independent Savères Mavic 3E RTK scene from 1,066 source
  photographs. COLMAP registered and undistorted 1,065 images (99.91%) with
  642,161 sparse points, 1.265 px mean reprojection error, and 0.078 m median
  Euclidean GPS residual.
- Replayed dev16, DC=0.010, and DC=0.020 for 500 and 1,000 steps with the exact
  same dev.21 binary (`96b9edc6...f146df4`) and dataset fingerprint.
- At 1,000 steps, Savères dev16 reaches 16.65243 dB / 0.131453 SSIM.
  DC=0.010 reaches 16.83870 dB / 0.131405, improving PSNR by +0.18628 dB but
  remaining effectively neutral in SSIM (-0.000048).
- DC=0.020 reaches 16.79856 dB / 0.132098, improving the Savères control by
  +0.14613 dB / +0.000644 SSIM and winning on 132/134 PSNR and 103/134 SSIM
  views.
- Across the 306 held-out Albagnac and Savères views, DC=0.020 improves the
  same-binary controls by +0.13101 dB / +0.001065 SSIM and wins on 303/306
  PSNR and 264/306 SSIM views. DC=0.010 gives the larger mean PSNR gain
  (+0.16354 dB) but lower SSIM coverage (204/306 views).
- The quality gain is not a speed gain. On Savères, DC=0.010 and DC=0.020
  increase 1,000-step manifest wall time by 14.6% and 19.3% versus dev16;
  Albagnac increases were 6.8% and 8.2%.
- Keep `dronegs-dev16` as the default throughput profile. Promote
  `calibrated-dc-0.020-opacity` to the recommended quality profile, without
  making it the global default before LPIPS and a larger-scene throughput gate.
- Dev.22 changes validation evidence, recommendation, version identifiers, and
  documentation only; the dev.21 optimizer and GPL-covered CUDA behavior are
  unchanged.
- Rebuilt the Release/sm_89 binary and passed all five native CPU/CUDA test
  executables.

## 0.5.0-dev.21 - Phase 4 intermediate-DC calibration

- Added `calibrated-dc-0.005-opacity`, `calibrated-dc-0.010-opacity`, and
  `calibrated-dc-0.020-opacity`; each changes only DC learning rate and epsilon
  plus the already isolated LichtFeld opacity family.
- Extended CLI validation, schedule events, manifest fields, native CUDA
  schedule tests, version identifiers, and GPL provenance.
- Passed all five native CPU/CUDA test executables.
- Replayed the dev16 control, both dev.20 endpoints, and all three intermediate
  rates for 500 steps with the exact same binary on the 1,376-image Albagnac
  split.
- At 500 steps, DC=0.010 gives the best mean PSNR gain (+0.18180 dB), while
  DC=0.020 gives the best mean SSIM gain (+0.001563) and improves SSIM on
  154/172 held-out views.
- Validated dev16, DC=0.010, and DC=0.020 for 1,000 steps with the same binary.
  DC=0.010 reaches 17.66035 dB / 0.252962 SSIM, improving the control by
  +0.14583 dB / +0.001328 and winning on 168/172 PSNR and 143/172 SSIM views.
- DC=0.020 reaches 17.63374 dB / 0.253027 SSIM, improving the control by
  +0.11923 dB / +0.001393 and winning on 171/172 PSNR and 161/172 SSIM views.
- Retain DC=0.010 as the primary balanced-quality candidate and DC=0.020 as the
  robust-view candidate. Keep dev16 as the default pending a second-scene
  replication and LPIPS.

## 0.5.0-dev.20 - Phase 4 DC-plus-opacity combination

- Added `lichtfeld-dc-opacity`, combining the two promising dev.19 families
  while retaining dev16 position, scale, rotation, and their epsilons.
- Extended CLI, manifest, JSON schedule events, version identifiers, direct
  CUDA schedule tests, and GPL provenance.
- Passed all five native CPU/CUDA test executables.
- The 500-step gate reaches 17.20034 dB / 0.245740 SSIM, approximately
  +0.1284 dB / +0.000239 versus the dev16 quality anchor.
- Replayed dev16 control, opacity-only, and DC-plus-opacity for 1,000 steps
  with the exact same final binary and five topology refinements.
- At 1,000 steps, opacity-only improves the control by +0.00706 dB /
  +0.000257 SSIM and wins on 109/172 PSNR and 130/172 SSIM views.
- DC-plus-opacity improves the control by +0.11791 dB / +0.000150 SSIM and
  wins on 142/172 PSNR views, but SSIM regresses on 106/172 views.
- Keep dev16 as the default. Retain opacity-only as the most homogeneous
  candidate and DC-plus-opacity as the best-PSNR candidate.
- The next calibration should sweep intermediate DC rates with LichtFeld
  opacity rather than selecting either endpoint.

## 0.5.0-dev.19 - Phase 4 MRNF one-family optimizer ablations

- Added five explicit profiles that replace exactly one dev16 parameter family
  with the pinned LichtFeld rate, schedule, normalization, and Adam epsilon.
- Refactored the optimizer to use an independent epsilon for DC, opacity,
  position, scale, and rotation; mixed profiles no longer leak epsilon changes
  into control families.
- Extended CLI, schedule JSON, manifest v1, CUDA tests, version identifiers,
  and GPL provenance for the family-isolated experiment.
- Replayed a same-binary dev16 control and all five 500-step ablations on the
  1,376-image Albagnac split.
- Position-only loses 1.12720 dB and 0.029407 SSIM versus the control,
  regressing 170/172 PSNR views and every SSIM view. Reject it.
- DC-only gains 0.08181 dB on 110/172 views but loses 0.000269 SSIM on
  average; retain it as a tradeoff candidate, not the default.
- Opacity-only gains 0.01622 dB and 0.000283 SSIM, improving 137/172 PSNR
  views and 142/172 SSIM views. It is the first no-compromise optimizer
  candidate, pending a longer-budget confirmation.
- Scale-only and rotation-only are effectively neutral at 500 steps.
- Keep `dronegs-dev16` as the default. The next optimizer experiment should
  combine DC and opacity without changing position, then validate at a longer
  iteration budget.

## 0.5.0-dev.18 - Phase 4 MRNF effective-update calibration

- Kept both optimizer configurations in one binary as
  `dronegs-dev16` and `lichtfeld-absolute`; restored the accepted dev.16
  quality profile as the CLI and training-context default.
- Added deterministic sampled optimizer telemetry at step 1, every training
  fifth, and the final step for DC, opacity, position, scale, and rotation.
- Telemetry reports incoming gradient RMS, actual post-clamp/post-normalization
  update RMS, resulting parameter RMS, and component sample count.
- Added the selected profile and its exact schedule constants to CLI parsing,
  JSON schedule events, manifest v1, and direct CPU/CUDA tests.
- Replayed both profiles for 500 steps on the same 1,376-image Albagnac split
  with the exact same dev.18 binary.
- `dronegs-dev16` reproduces the quality anchor at 17.07045 dB / 0.245493
  SSIM; `lichtfeld-absolute` reaches 16.11581 dB / 0.219508 SSIM.
- At step 1, gradients are identical but dev16 applies 20.35x larger DC and
  58.24x larger position updates, while its opacity update is 0.677x as large.
  The discrepancy persists throughout the run.
- Reject a single global LR correction. Dev.19 should run one-family
  ablations, beginning with DC and position, before changing opacity, scale,
  or rotation.

## 0.5.0-dev.17 - Phase 4 MRNF optimizer schedule isolation

- Replaced the dev.16 optimizer constants with the pinned LichtFeld MRNF
  profile for DC, opacity, scale, rotation, position, and Adam epsilon.
- Added the MRNF 80% spatial bound: the median of the three initial Gaussian
  axis widths between the 10th and 90th percentiles.
- Added exponential position `2e-5 -> 2e-7` and scale `0.007 -> 0.005`
  schedules using the optimizer step and total iteration count.
- Added a public read-only learning-rate diagnostic plus initial/final JSON
  events and exact manifest fields.
- Added direct CUDA tests for the 80% bound, all five initial learning rates,
  epsilon `1e-15`, first-step behavior, and exponential decay.
- Quantified the Albagnac position LR change from `0.00832225` in dev.16 to
  `0.000135236` in dev.17, a 61.5x reduction; DC falls 25x.
- The 500-step run ends at 1,173,577 Gaussians but regresses dev.16 by
  0.95662 dB and 0.026034 SSIM; 169/172 PSNR views and all SSIM views regress.
- Reject direct absolute-LR copying as a quality solution. The result proves
  that DroneGS gradient/parameter scale must be calibrated before using
  LichtFeld's optimizer values.
- Extended the GPL provenance entry to the inspected LichtFeld optimizer,
  schedule, bounds, and Adam sources.

## 0.5.0-dev.16 - Phase 4 reproducible Gumbel and edge guidance

- Replaced deterministic descending-score growth selection with weighted
  Gumbel top-K over the existing MRNF refinement score.
- Made selection reproducible by deriving each refinement seed from the CLI
  seed and iteration, then each per-Gaussian variate with SplitMix64.
- Added a luminance Sobel map on each already-scheduled training view and
  accumulated edge-weighted alpha contribution in the existing backward pass.
- Normalized positive per-Gaussian edge scores by their refinement-window
  median and applied LichtFeld's `1 + 0.25 * normalized_edge` guidance factor.
- Avoided LichtFeld's extra Canny plus full-raster passes over at least 8% of
  the dataset at every refinement; dev.16 performs zero extra edge renders.
- Added CUDA coverage proving same-seed bit-identical growth, different-seed
  selection, and retained split/capacity/statistic correctness.
- On Albagnac, dev.16 ends at 1,173,573 Gaussians, three below dev.15, and
  improves held-out quality by 0.01195 dB and 0.000550 SSIM.
- Trainer compute rises from 55.865 to 60.683 seconds (+8.6%); the small
  quality gain does not yet justify accepting the overhead as final.
- Extended the GPL provenance entry to the inspected LichtFeld Gumbel and edge
  rasterizer sources; the same two combined CUDA units remain GPL-covered.

## 0.5.0-dev.15 - Phase 4 MRNF growth isolation

- Added capacity-aware persistent Gaussian, gradient, statistic, and Adam
  buffers up to `--max-cap`.
- Added SSIM-error-map normalization and per-Gaussian contribution-weighted
  refinement statistics accumulated over 200-step windows.
- Added deterministic score/index selection at threshold `0.003`, 7% growth,
  and a rotated longest-axis parent/child split with reset optimizer moments.
- Added topology events with candidate/addition counts and manifest fields for
  protocol, refinement count, total additions, and final population.
- Added a forced 1-to-2 CUDA split test covering geometry, copied attributes,
  opacity, capacity, and statistic reset; all five native executables pass.
- The Albagnac 500-step run grows by 148,483 to 1,173,576 Gaussians, only 36
  above pinned LichtFeld's final population.
- Held-out quality regresses dev.14 by 0.0556 dB and 0.001321 SSIM while
  trainer compute rises 36.1%; population parity alone is rejected as a
  quality solution.
- Conservatively relicensed `cuda/rasterization.cu` and `cuda/trainer.cu`
  under GPL-3.0-or-later because dev.15 adapts pinned LichtFeld MRNF behavior;
  exact upstream/local paths are recorded in `GPL_COMPONENTS.md`.

## 0.5.0-dev.14 - Phase 4 analytical DSSIM objective

- Replaced the ordered trainer's pure active-pixel L1 objective with
  `0.8 * L1 + 0.2 * (1 - SSIM)`, retaining the dev.13 split, topology,
  rasterizer, schedules, and targets.
- Reused the separable 11x11 CUDA SSIM forward and added an analytical
  atomics-free image gradient that gathers at most 121 valid window centers
  per input sample.
- Added a public diagnostic output plus a direct CPU objective oracle and
  eight central finite-difference probes of the exact trainer gradient.
- Recorded the loss formula and `lambda_dssim=0.2` in manifest v1 and kept
  the independently implemented MIT provenance explicit.
- All five native test executables pass on the RTX 4070 Laptop.
- On the identical 1,376-image Albagnac split and 500-step schedule, mean
  held-out SSIM rises from 0.241900 to 0.246278 while PSNR changes from
  17.121187 to 17.115355 dB.
- DSSIM improves SSIM on 171 of 172 views, costs 4.4% more trainer compute,
  and does not close the topology/quality gap to pinned LichtFeld.

## 0.5.0-dev.13 - Phase 4 held-out quality gate

- Added an opt-in LichtFeld-compatible split where
  `scene_index % test_every == 0` is excluded from every Adam schedule.
- Added persistent CUDA PSNR and separable Gaussian 11x11 SSIM evaluation
  using LichtFeld's data range and valid-padding conventions.
- Added initial/final aggregate metrics, per-view CSV output, active-pixel
  coverage, evaluation timings, and optional lossless PPM predictions.
- Extended CLI and manifest v1 with the split, metric protocol, counts, and
  held-out artifacts while leaving legacy no-evaluation runs unchanged.
- Added deterministic split tests and a direct CPU oracle for GPU PSNR/SSIM.
- On Albagnac, reserved 172 of 1,376 views and improved held-out quality from
  14.0631 to 17.1212 dB and from 0.1811 to 0.2419 SSIM over 500 iterations.
- Ran the pinned GPL LichtFeld control on the identical split and settings:
  21.0686 dB, 0.6310 SSIM, and 1,173,540 final Gaussians.
- Kept the Phase 4 tag open: the gaps are 3.9474 dB and 0.3891 SSIM; LPIPS
  remains null because no local model/runtime is installed.

## 0.5.0-dev.12 - Phase 4 persistent geometry Adam

- Retained projected-conic and position/log-scale/quaternion gradients in the
  persistent CUDA context without per-step host readback.
- Added first/second Adam moments for all ten geometry parameters.
- Added a scene-diagonal-scaled position LR decaying from `1.6e-4` to
  `1.6e-6`, scale LR `0.005`, and rotation LR `0.001`.
- Bounded log-scales to the initialized global range plus/minus 4 and
  renormalized every quaternion after its update.
- Extended convergence coverage to require finite geometry, movement in all
  three parameter families, and unit quaternions.
- Reduced Albagnac anchor L1 from 0.200559 to 0.104295 over 500 iterations,
  versus 0.155307 with geometry fixed.
- Measured 39.552 seconds of trainer compute, 41.851 seconds wall, and an
  838 MiB sampled total-VRAM delta with no OOM.

## 0.5.0-dev.11 - Phase 4 anisotropic geometry backward

- Extended public backward with position, three log-scale, and normalized
  `w,x,y,z` quaternion gradients.
- Added a CPU finite-difference oracle and an analytical CUDA reverse chain
  through inverse covariance, spectral clamp, perspective, scale, and rotation.
- Added direct finite differences for all ten geometry components on a
  branch-stable anisotropic fixture.
- Measured 40.528 ms forward and 91.601 ms forward+geometry-backward medians
  at 1,025,093 Gaussians / 800x580.
- Completed an Albagnac 500-iteration regression in 32.233 seconds, reducing
  anchor L1 from 0.200559 to 0.155307 with no OOM.
- Kept persistent training on DC/opacity Adam; geometry integration is dev.12.

## 0.5.0-dev.10 - Phase 4 anisotropic covariance forward

- Replaced the projected scalar sigma with an inverse 2D conic and independent
  axis-aligned support radii.
- Added normalized quaternion rotation, non-uniform exponential scales, camera
  rotation, and the full perspective Jacobian to the CPU and CUDA projection.
- Added spectral clamping of both projected covariance eigenvalues to
  `[0.75², 8²]` pixels, preserving the previous footprint safety bounds.
- Routed the anisotropic conic through tiled forward rendering, reverse
  composition, persistent training, tile bounds, culling, and statistics.
- Added CPU rotation/swap, zero-quaternion rejection, extreme-scale clamp, and
  CUDA forward/backward parity tests with rotated cameras and conics.
- Measured 44.934 ms forward and 64.416 ms forward+backward medians at
  1,025,093 Gaussians / 800x580 across two order-balanced seven-run sets.
- Completed the real 1,376-image Albagnac 500-iteration run in 30.316 seconds,
  reducing anchor L1 from 0.200559 to 0.155307 with no OOM.
- Kept geometry fixed: position, scale, and rotation gradients are the next
  correctness sub-gate.

## 0.5.0-dev.9 - Phase 4 bounded JPEG decode experiments

- Replaced the single outstanding prefetch state with an ordered, bounded
  queue that supports multiple decoder workers without concurrent LRU mutation.
- Added concurrency, queue-capacity, refill, duplicate-decode, and CLI tests.
- Added optional `--prefetch-depth`, `--decode-workers`, and
  `--jpeg-idct-scale` controls and recorded them in the run manifest.
- Added an opt-in reduced-IDCT libjpeg path that decodes at the closest native
  1/2, 1/4, or 1/8 scale before any final resize.
- Benchmarked five short queue configurations and three 500-iteration
  Albagnac configurations.
- Rejected multi-worker decode as the default: depth 8 / two workers removed
  nearly all foreground wait but was 3.6% slower than the single-worker
  control because of CPU/GPU power contention on the laptop.
- Kept reduced IDCT opt-in: its 500-iteration wall time was 2.1% shorter than
  the same-cycle control, but its filtered target pixels changed anchor L1 and
  require held-out quality validation.
- Preserved the dev.8 defaults: one prefetch slot, one worker, full JPEG decode.

## 0.5.0-dev.8 - Phase 4 persistent ordered-alpha trainer

- Added an opaque persistent CUDA training context that retains Gaussians,
  projected records, CUB storage, tile pairs/ranges, image gradients, and Adam
  moments across iterations.
- Added grow-on-demand pair and tile capacities so repeated camera frames avoid
  per-iteration CUDA allocation after reaching their high-water marks.
- Connected RGB8 active-pixel L1, ordered-alpha backward, and DC/opacity Adam
  entirely on device; only pair-count and loss/active-pixel scalars return to
  the host during a step.
- Switched the experimental DroneGS binary to the ordered-alpha trainer and
  retained the additive trainer as a synthetic convergence control.
- Added side-by-side additive and ordered-alpha convergence coverage.
- Completed a real 1,376-image / 1,025,093-Gaussian Albagnac run at 500
  iterations in 25.80 seconds in Release/sm_89, reducing anchor L1 from
  0.200559 to 0.155306.
- Measured 14.35 seconds of trainer compute and a 651 MiB sampled peak
  total-VRAM delta; JPEG foreground wait is now a material 10.32-second cost.
- Renamed the manifest mode to
  `dronegs-fixed-topology-ordered-alpha-prototype`.

## 0.5.0-dev.7 - Phase 4 ordered-alpha backward

- Added public ordered-alpha backward outputs for DC color and opacity-logit
  gradients alongside the matching forward render.
- Added an original CPU reference that reverses each pixel's contributing
  sequence while carrying the composited tail color.
- Added a tiled CUDA backward kernel that reconstructs pre-splat
  transmittance, handles alpha/contribution clamps, and atomically accumulates
  per-Gaussian DC and opacity gradients.
- Added CPU and direct CUDA finite-difference checks plus CPU/CUDA parity for
  equal depths, multi-tile coverage, empty scenes, and early exit.
- Extended the opt-in raster benchmark with a forward+backward mode.
- Measured 52.889 ms combined median for forward+backward at 1,025,093 splats
  and 800x580 in Release/sm_89, versus 35.190 ms for forward alone.
- Kept the production trainer additive: the validated API still performs
  per-call allocation and host readback and is not yet a persistent training path.

## 0.5.0-dev.6 - Phase 4 GPU tile pipeline

- Moved visible-splat projection and tile-bound calculation from the host to CUDA.
- Added deterministic depth keys combining positive float depth bits with source
  index, followed by a stable CUB radix sort.
- Added a CUB exclusive scan for tile-pair offsets, GPU pair duplication, stable
  tile/depth sorting, and GPU tile-range construction.
- Removed host projected-splat vectors, per-tile vectors, and their transfers
  from the ordered-alpha forward path.
- Added equal-depth source-order and explicit multi-tile CPU/CUDA parity tests.
- Added an opt-in, reproducible end-to-end CUDA raster benchmark.
- Reduced the 1,025,093-splat / 800x580 benchmark median from 146.311 ms to
  35.395 ms across two order-balanced five-run sets on the RTX 4070 Laptop:
  4.13x faster and 75.81% less wall time.
- Kept the production trainer on the additive backward path; ordered-alpha
  backward, anisotropic covariance, and held-out quality parity remain open.

## 0.5.0-dev.5 - Phase 4 tiled-alpha CUDA forward

- Added a 16x16 tiled CUDA front-to-back alpha renderer.
- Added host reference binning into per-tile splat lists that preserve stable
  global depth order.
- Loaded splat batches cooperatively into shared memory and rendered one thread
  per pixel without RGB or transmittance atomics.
- Matched CPU RGB, residual transmittance, evaluated-pair, and
  contributing-pair outputs on multi-tile synthetic scenes.
- Added CUDA coverage for backgrounds, input-order independence, empty scenes,
  culling, contribution thresholds, and early-transmittance exit.
- Kept the trainer on the validated additive backward path; GPU binning,
  anisotropic covariance, and ordered-alpha backward remain open.

## 0.5.0-dev.4 - Phase 4 ordered-alpha oracle

- Added an original CPU reference for depth-sorted front-to-back alpha composition.
- Defined the raster camera, RGB, transmittance, and contribution-stat contracts.
- Matched the current projection and isotropic support rules while introducing
  bounded alpha, minimum-contribution, and early-transmittance thresholds.
- Added tests for single-splat contribution, depth order, background, culling,
  transmittance, and invalid cameras.
- Kept the CUDA training path additive until a tiled forward renderer matches
  the oracle and its backward pass is validated.
- Kept `dronegs-v0.5.0` untagged; this is a correctness foundation, not parity.

## 0.5.0-dev.3 - Phase 4 large-scene decode overlap

- Added a persistent, single-slot JPEG prefetch worker without concurrent LRU mutation.
- Precomputed the deterministic camera schedule and overlapped decode N+1 with render N.
- Split total JPEG service time from foreground image-wait time in run-manifest v1.
- Added prefetch started, consumed, and ready counters plus concurrency tests.
- Kept the 256 MiB resident LRU bound; one decoded image may additionally be in flight.
- Reduced median Albagnac image wait to 0.954 s and median 500-iteration wall
  time to 59.63 s at equivalent anchor loss.
- Improved warm end-to-end wall time by 15.9% versus a same-session dev.2
  control on 1,376 images and 1,025,093 Gaussians.
- Kept `dronegs-v0.5.0` untagged; ordered alpha compositing and quality parity remain open.

## 0.5.0-dev.2 - Phase 4 large-scene memory

- Changed decoded training targets and GPU transfers from float32 RGB to RGB8.
- Replaced eager all-image decoding with a lazy 256 MiB byte-bounded LRU cache.
- Added cache hit, miss, eviction, capacity, peak-residency, and decode timings.
- Added a 2,048-image cardinality stress test for the memory bound.
- Reduced the GAJAN-25 decoded target peak by 75% to 15.12 MB.
- Reduced the median 500-iteration training loop by 11.6% at equivalent anchor loss.
- Passed a real 1,376-image / 1,025,093-Gaussian run in 247.4 seconds with
  267.3 MB peak decoded residency and 309 cache evictions.
- Kept `dronegs-v0.5.0` untagged; this is a scaling sub-gate, not quality parity.

## 0.5.0-dev.1 - Phase 4 experimental

- Preserved COLMAP world-to-camera poses and added native JPEG decoding.
- Added PINHOLE and SIMPLE_PINHOLE projection with resized intrinsics.
- Added an original CUDA additive Gaussian rasterizer and analytical backward pass.
- Added Adam optimization for DC color and opacity with deterministic camera order.
- Added initial/final anchor loss and training time to run-manifest v1.
- Added an end-to-end GPU convergence test and a GAJAN 25-image smoke report.
- Kept positions, scales, rotations, topology, and non-DC SH coefficients fixed.
- Did not tag `dronegs-v0.5.0`: ordered alpha compositing and the fixed-topology
  quality parity exit gate are not complete.

## 0.4.0 - Phase 3

- Added the original MIT C++23/CUDA DroneGS project and development image.
- Added strict parsing of trainer CLI contract v1.
- Added bounded COLMAP binary camera, image, and sparse-point loading.
- Added fixed-topology Gaussian initialization and atomic binary PLY export.
- Added atomic run-manifest-v1 output with Git provenance.
- Added native COLMAP/CLI/PLY tests and a finite-difference CUDA gradient test.
- Verified the native PLY with DroneAI's existing CuPy `GaussianModel` on GPU.
- Added a containerized LichtFeld baseline suite and Docker-aware VRAM sampling.

## 0.3.0 - Phase 2

- Added a validated backend-neutral training request and normalized result.
- Added LichtFeld and contract-v1 DroneGS subprocess adapters.
- Kept LichtFeld as the default while adding explicit environment and mission selection.
- Wired the existing partitioned orthophoto workflow through the backend boundary.
- Documented the pinned LichtFeld CLI's lack of user-controlled seed support.

## 0.2.0 - Phase 1

- Added a versioned, backend-neutral benchmark suite format.
- Added isolated repeated runs with immutable output directories.
- Added dataset inventory fingerprints and PLY artifact validation.
- Added wall-time summaries and best-effort per-process VRAM sampling.
- Added the five-run GAJAN LichtFeld reference suite.

## 0.1.0 - Phase 0

- Defined the product boundary between DroneAI and its Gaussian trainer.
- Versioned the initial CLI and run-manifest contracts.
- Added the implementation roadmap and phase gates.
- Added the GPL and third-party provenance register.

The production backend remains LichtFeld until all parity gates pass.
