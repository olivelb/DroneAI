# DroneGS architecture

Status: Phase 3 released; Phase 4 experimental trainer in progress

Contract version: 1  
Project version: 0.5.0-dev.9

## Decision

DroneGS will be a standalone, headless Gaussian Splatting trainer specialized
for DroneAI. It consumes an undistorted COLMAP dataset and emits a standard
3DGS PLY plus a versioned run manifest. Existing filtering, partition merge,
geospatial transforms, orthographic rendering, GeoTIFF writing, and detection
remain outside DroneGS.

LichtFeld remains the production backend until the quality, speed, VRAM, and
compatibility gates in `ROADMAP.md` pass.

## Stable boundary

```text
COLMAP dense workspace
        |
        v
Gaussian trainer backend
   |                 |
LichtFeld          DroneGS
   |                 |
   +--- point_cloud.ply
            |
            v
existing DroneAI GaussianModel and ortho pipeline
```

The integration boundary is a subprocess. DroneAI does not link to trainer
internals. A backend adapter may translate canonical arguments to legacy
LichtFeld spellings.

## In scope

- COLMAP binary model loading for undistorted pinhole datasets.
- Sparse-point initialization of 3D Gaussians.
- CUDA differentiable Gaussian rasterization.
- L1 and DSSIM photometric loss.
- Adam with parameter-specific schedules and progressive spherical harmonics.
- A strategy interface with MRNF-compatible behavior.
- Checkpoint/resume, deterministic seeds, cancellation, metrics, and PLY export.
- Single-GPU training first; bounded large-scene partitioning after parity.

## Out of scope before 1.0

- GUI, editing, viewport, video, USD, mesh, SOG/SPZ, plugins, Python, and MCP.
- General distorted, fisheye, equirectangular, or dynamic-scene training.
- Replacing DroneAI's CuPy orthographic renderer.
- Removing the LichtFeld fallback before the gates pass.

## Technology choices

- C++23 control plane and CUDA 12.8 or newer.
- CMake and Ninja.
- Purpose-built structure-of-arrays buffers rather than a general tensor API.
- CTest-driven native unit, finite-difference, and convergence checks.
- JSON Lines progress events and a versioned JSON final manifest.
- Standard 3DGS PLY as the inference/export boundary.

Any adapted source is recorded in `GPL_COMPONENTS.md` with its exact upstream
revision, license, copyright, and local paths.

## Phase 4 development slice

The current development slice projects fixed sparse Gaussians with COLMAP
world-to-camera poses, sorts visible splats deterministically into 16x16 tiles,
composites bounded isotropic kernels front-to-back, computes active-pixel L1,
back-propagates analytical DC/opacity gradients, and applies Adam on device.

This is still a fixed-topology convergence scaffold, not the parity rasterizer.
It has no anisotropic covariance projection, position/scale/rotation gradients,
DSSIM, progressive SH, split/prune/grow, or held-out quality evaluation. It
supports only SIMPLE_PINHOLE and PINHOLE inputs.

Decoded RGB targets are stored as bytes in a lazy 256 MiB LRU cache. Resident
payload therefore stays bounded independently of image count, and cache
hit/miss/eviction/peak-byte metrics are recorded. A bounded ordered queue can
feed one or more persistent decoder workers while the main thread remains the
sole owner of LRU mutation. The measured default stays at one slot and one
worker. The manifest separates cumulative decoder service time from foreground
wait and records the queue depth, worker count, and reduced-IDCT mode.
Pinned double-buffered target staging was prototyped and rejected for this
slice: measured upload service was about 0.06 seconds per 500-iteration
Albagnac run, below timing variance and not worth the added synchronization.

Version 0.5.0-dev.4 adds a separate CPU correctness oracle for stable,
depth-sorted front-to-back alpha composition. It records RGB, residual
transmittance, visible splats, evaluated pairs, and contributing pairs. The
0.5.0-dev.5 adds a matching forward CUDA implementation: per-tile depth lists
feed 16x16 CUDA blocks, and each block cooperatively stages splat batches in
shared memory. Version 0.5.0-dev.6 moves projection, deterministic depth sorting,
tile-pair duplication and sorting, and tile-range construction to CUDA with CUB.
Version 0.5.0-dev.7 adds CPU and CUDA backward paths for DC color and opacity.
For each pixel, reverse composition carries the color produced by all later
splats and reconstructs pre-splat transmittance from the final residual. This
matches finite differences while respecting contribution clamps and early exit.
Version 0.5.0-dev.8 adds a separate persistent training context. Gaussian,
projection, CUB temporary, tile, gradient, and Adam allocations survive across
iterations and grow only when a camera exceeds a previous pair/tile high-water
mark. RGB8 targets are uploaded per frame; L1 image gradients, reverse
composition, and Adam remain on device. The experimental DroneGS binary now uses
this ordered-alpha path. The additive implementation remains a test control,
while LichtFeld remains the production backend until quality gates pass.
Version 0.5.0-dev.9 generalizes the single prefetch slot into a bounded ordered
queue and adds an opt-in libjpeg reduced-IDCT path. Multi-worker decode and
reduced IDCT remain measured experiments rather than defaults: the former
regressed 500-iteration Albagnac wall time through laptop CPU/GPU power
contention, while the latter changes filtered target pixels and lacks a
held-out quality gate.

## Planned layout

```text
app1-colmap/dronegs/
    CMakeLists.txt
    include/dronegs/{api,colmap,model,rasterization,training}/
    src/{api,colmap,model,rasterization,training}/
    cuda/{rasterization,losses,optimizer,strategies}/
    tests/
    benchmarks/

app1-colmap/gaussian_training/
    Python backend boundary and benchmark support
```

## Reproducibility

Run-manifest v1 reserves trainer, source, hardware, parameter, timing, metric,
and artifact provenance. The development prototype records its version, Git
revision, contract, parameters, seed, dataset fingerprint, training losses,
timings, and final Gaussian count. GPU/driver/peak-VRAM fields and artifact
hashes are not yet populated by the native binary. Benchmark seeds are
mandatory. Dataset inputs are read-only and outputs use a new run directory.

## Large-scene performance principles

- asynchronous decode and host-to-device transfer;
- bounded GPU image cache and double buffering;
- persistent allocations up to Gaussian capacity;
- fused loss and optimizer kernels;
- CUDA Graph capture between topology changes when profitable;
- spatial partitioning without loading all full-resolution images at once;
- separate startup, loading, training, checkpoint, filtering, and ortho timing.

Performance changes are accepted only at equal measured quality.
