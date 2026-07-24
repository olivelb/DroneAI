# DroneGS architecture

Status: Phase 3 released; Phase 4 experimental trainer in progress

Contract version: 1  
Project version: 0.5.0-dev.1

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
world-to-camera poses, rasterizes bounded isotropic screen-space kernels into
weighted RGB accumulators, computes active-pixel L1, back-propagates analytical
gradients, and updates DC color plus opacity with Adam.

This is a gradient/convergence scaffold, not the parity rasterizer. It has no
front-to-back alpha ordering, visibility sort, anisotropic covariance projection,
position/scale/rotation gradients, DSSIM, progressive SH, split/prune/grow, or
held-out quality evaluation. It supports only SIMPLE_PINHOLE and PINHOLE inputs.

The prototype currently caches all decoded training images in host RAM. That is
acceptable for the 25-image smoke gate but explicitly blocks representative
1,000+ image tests until a bounded asynchronous image cache is implemented.

## Planned layout

```text
app1-colmap/dronegs/
    CMakeLists.txt
    include/dronegs/{api,colmap,model,rasterization,training}/
    src/{api,colmap,model,rasterization,training}/
    cuda/{rasterization,losses,optimizer,strategies}/
    tests/

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
