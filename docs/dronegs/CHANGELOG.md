# DroneGS changelog

This changelog covers the standalone Gaussian trainer project.

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
