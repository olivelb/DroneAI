# DroneGS changelog

This changelog covers the standalone Gaussian trainer project.

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
