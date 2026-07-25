# DroneGS architecture

Status: Phase 3 released; Phase 4 experimental trainer in progress

Contract version: 1  
Project version: 0.5.0-dev.16

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

The current development slice initializes sparse Gaussians from COLMAP
world-to-camera poses, transforms normalized quaternion rotations and
non-uniform exponential scales into camera space, projects the covariance
through the pinhole Jacobian, sorts visible splats deterministically into 16x16
tiles, composites bounded anisotropic kernels front-to-back, computes
`0.8 * active-pixel L1 + 0.2 * (1 - SSIM)`, and exposes analytical image, DC,
opacity, position, log-scale, and normalized-quaternion gradients. The
persistent trainer retains those
gradients and all Adam moments on device and updates the five parameter
families after every training frame. At 200-step windows it can grow topology
up to `max_cap` using normalized SSIM-error-weighted alpha contributions and a
rotated longest-axis parent/child split.

This is still an incomplete MRNF scaffold, not the parity rasterizer. It has
growth and split but no prune/replacement, Gumbel sampling, edge guidance,
noise, decay, compaction, progressive SH, or LPIPS. It supports only
SIMPLE_PINHOLE and PINHOLE inputs.

An opt-in held-out protocol uses the same index rule as LichtFeld:
`scene_index % test_every == 0`. Those descriptors never enter the shuffled
training schedule. Initial and final passes report full-frame RGB PSNR and
Gaussian 11x11 SSIM with sigma 1.5 and valid padding. PSNR/SSIM reductions
remain on CUDA; optional final predictions are downloaded only for lossless
PPM export and external perceptual evaluation.

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
Version 0.5.0-dev.10 replaces scalar projected sigma with a full symmetric 2D
covariance. The implementation forms
`J * R_camera * R_gaussian * diag(exp(log_scale))`, multiplies that matrix by
its transpose, clamps both eigenvalues to the previous pixel-footprint safety
range, and stores the inverse conic. CPU and CUDA use the same quaternion
`w,x,y,z` convention and perspective Jacobian. Scale and rotation affect the
forward image but remain fixed until their persistent updates are implemented.
Version 0.5.0-dev.11 extends the public backward result with position,
log-scale, and quaternion gradients. CUDA accumulates projected-center and
inverse-conic adjoints per pixel, then reverses inverse covariance, the
piecewise spectral clamp, perspective Jacobian, camera transform, exponential
scale, rotation matrix, and quaternion normalization once per Gaussian.
Discrete visibility, tile/support selection, depth sorting, contribution
thresholds, and early exit are stop-gradient decisions. A CPU oracle and a
direct all-component CUDA finite-difference fixture guard the smooth branch.
That version intentionally deferred persistent geometry Adam.
Version 0.5.0-dev.12 adds persistent gradients and first/second moments for
position, log-scale, and quaternion rotation. Position LR is the initial
Gaussian bounding-box diagonal times an exponential `1.6e-4` to `1.6e-6`
schedule over the requested training steps. Log-scale uses `0.005`, rotation
uses `0.001`, log-scales stay within four natural-log units of the initialized
global range, and quaternions are normalized after every update. No geometry
gradient or moment is read back during training.
Version 0.5.0-dev.13 adds a deterministic train/held-out split and persistent
quality workspace. Squared-error samples and separable SSIM moments are
reduced with CUB; only per-view scalar metrics return to the host unless
lossless prediction export is requested. The manifest records the split rule,
metric constants, train/held-out cardinalities, initial/final aggregates, and
per-view CSV artifact. The matching Albagnac control is executed through the
pinned GPL LichtFeld runtime image; no LichtFeld metric source is copied into
the original MIT CUDA implementation.
Version 0.5.0-dev.14 reuses that separable SSIM forward in every ordered
training step and stores five derivative terms per valid window center.
One CUDA thread per input RGB sample gathers the at-most 121 overlapping
centers, avoiding atomics in the image-space DSSIM backward. The public
diagnostic returns the rendered image, transmittance, objective, and exact
trainer image gradient; a CPU oracle plus eight central finite differences
guard the implementation. The Albagnac result isolates DSSIM from topology:
SSIM improves by 0.004378 while PSNR is effectively unchanged, leaving MRNF
growth as the next controlled parity factor.
Version 0.5.0-dev.15 preallocates parameter, gradient, statistic, and Adam
capacity to `max_cap`. During backward it accumulates each Gaussian's alpha
blending weight and the same weight multiplied by a normalized `1-SSIM` map.
Every 200 steps, candidates above `0.003` are sorted deterministically by
weight/source index; 7% are split along the longest rotated 3D scale axis,
with 0.5/0.85 scale shrink, opacity redistribution, appended children, and
zeroed parent/child moments. This reproduces LichtFeld's final 500-step
Albagnac population within 36 Gaussians but slightly reduces held-out quality.
It therefore proves that population count and split geometry alone do not
explain parity.

Version 0.5.0-dev.16 replaces the deterministic descending selection with
weighted Gumbel top-K. The refinement seed is a stable function of the CLI
seed and iteration; SplitMix64 generates one open-interval uniform variate per
source index before applying `log(weight) - log(-log(u))`. During each
already-scheduled training backward, a luminance Sobel map contributes
`transmittance_before * alpha * edge_magnitude` to each Gaussian. At
refinement, positive edge scores are normalized by their median and multiply
the growth weight by `1 + 0.25 * normalized_edge`. This preserves LichtFeld's
guidance semantics without its separate Canny and additional full-dataset
raster samples. The tradeoff is measurable: a small held-out gain over dev.15
at an 8.6% trainer-compute cost.

The dev.15/dev.16 behavior was adapted after inspection of pinned LichtFeld GPL
sources. `cuda/rasterization.cu` and `cuda/trainer.cu` are consequently marked
GPL-3.0-or-later and recorded with exact upstream/local paths in
`GPL_COMPONENTS.md`. The remaining original DroneGS units retain their
existing MIT identifiers; the linked dev.15+ native binary is GPL-covered.

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
