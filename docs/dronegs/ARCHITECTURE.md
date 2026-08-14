# DroneGS architecture

Status: dev.48 sole production Gaussian backend with tiled training and opacity-SH

Contract version: 1  
Project version: 0.5.0-dev.62

## Decision

DroneGS will be a standalone, headless Gaussian Splatting trainer specialized
for DroneAI. It consumes an undistorted COLMAP dataset and emits a standard
3DGS PLY plus a versioned run manifest. Existing filtering, partition merge,
geospatial transforms, orthographic rendering, GeoTIFF writing, and detection
remain outside DroneGS.

DroneGS is the production backend after passing the frozen Albagnac
quality/speed gate. Dev.46 removes the LichtFeld executable path and adds
full-state checkpoint/resume plus held-out deployment canaries. Dev.47 adds
strict production-profile/dataset/artifact identity, checkpoint V3 integrity
and spatial-block evaluation. LichtFeld is retained only in historical
comparisons and the GPL provenance record.

Dev.60 consumes the raw FastGS appearance derivatives directly in the scalar
and coefficient-parallel Adam kernels. DC scaling and SH basis expansion are
performed at their point of use, eliminating the expanded color/opacity-SH
gradient writes, reads and buffer clears while retaining moment updates for
temporarily invisible Gaussians.

Dev.59 accumulates the FastGS DC and opacity derivatives once per
source/tile, then expands the color-SH and opacity-SH basis products once per
active Gaussian. An exact-zero guard skips invisible Gaussians after four
coalesced reads. This removes up to 60 contended global atomics per
source/tile without changing the derivative equations or progressive-SH
contract.

Dev.31 replaces the scene-wide uniform initial Gaussian scale with the MRNF
local-neighborhood rule: an exact deterministic KD tree measures the two
nearest neighbours, while robust central extents provide a scene-relative
upper bound. This preserves isotropic initialization but follows local COLMAP
point density instead of forcing every splat to the same size.

Dev.32 permits SH-derived splat color in `[0,4]` during rendering and keeps
its gradient live over that interval. This matches the pinned FastGS color
contract and prevents coefficients just above display white from being frozen;
final RGB image serialization remains clamped to the display range.

Dev.58 bounds the standard Fast/Normal/HQ recovery cadence to one, two and
three snapshots for 7,500, 15,000 and 30,000 iterations. The training thread
synchronizes once to capture a complete immutable host snapshot; one bounded
background writer then computes the checksum, fsyncs and atomically publishes
the file. A later snapshot and trainer completion always join the preceding
writer, so failures remain fatal and no more than one snapshot is resident.
Manifest timings distinguish capture stall, completion wait and overlapped
write time.

Dev.57 treats refinement statistics as topology-lifecycle state rather than
an unconditional by-product of every optimizer step. The trainer derives the
last iteration whose statistics can reach a scheduled 200-step refinement and
then disables only the refinement error/edge maps and per-Gaussian statistic
accumulation. The differentiable render, geometry backward pass and Adam
updates continue unchanged, so cooldown remains fixed-topology convergence
rather than an abbreviated training phase. Objective-only evaluation never
mutates refinement state.

Dev.52 retains the exact Adam equations and progressive-SH schedule but moves
the active color-SH and opacity-SH coefficient updates out of the per-Gaussian
serial loop. One CUDA thread owns one coefficient, while DC, opacity, geometry
and optimizer telemetry stay in the original per-Gaussian kernel. The two
kernels share the same stream and bias-correction values; no parameter family
is reordered internally and no persistent device allocation is added.

Dev.33 recalibrates only opacity Adam after the much smaller local-KNN
footprints. The selected `0.096` quality profile retains DC `0.010`, opacity
epsilon `1e-15`, and all dev.32 geometry, topology, renderer, and portability
behavior. It improves PSNR, SSIM, and LPIPS on Albagnac, GAJAN, and Savères.

Dev.34 exposes isolated scale/rotation structure profiles. The combined
profile improves PSNR and SSIM on all three scenes but has a small Savères
LPIPS tradeoff, so it remains opt-in and does not replace dev.33.

Dev.38 adds an architecture-independent FastGS compatibility profile.
It treats projected covariance dilation, extended-FOV Jacobian clamping,
opacity-dependent support, alpha ceiling, and analytical backward as one
coupled renderer contract. It also adds a binary Gaussian PLY import path for
direct same-camera/same-split renderer cross-evaluation. On Albagnac, a
1,200-step dev.38 model exceeds the pinned LichtFeld PLY oracle rendered on
the identical 172 held-out views in both PSNR and SSIM.

Dev.39-dev.45 complete pruning parity, slot recycling, cached backward state,
structural FastGS buckets/checkpoints and warp-cooperative backward, bounded
scene-resident image caching, topology cooldown and progressive photometric
finish. On the frozen Albagnac 15,000-step control dev.45 is both faster and
better than deterministic LichtFeld on PSNR, SSIM and LPIPS.

Dev.46 serializes all parameters, optimizer moments, topology statistics,
schedule/RNG state and fingerprints to an atomically replaced checkpoint.
The Python boundary auto-resumes incomplete mission outputs and writes an
atomic PSNR/SSIM canary verdict before downstream rendering.

Dev.47 appends a checksum to checkpoint V3, fsyncs file and directory
publication, and preserves the last valid checkpoint on failure. Completed
reuse verifies the full sparse/image dataset identity, trainer binary hash,
profile, canary and PLY hash. The optional spatial split projects camera
centers onto their two dominant axes, holds a deterministic central block and
can exclude a guard ring from training; production V1 keeps modulo parity.

Dev.48 treats tile mode as image-space view expansion. Crops retain source
resolution up to the per-crop width ceiling; their focal lengths keep the
same pixel scale and their principal points are translated by the crop origin.
Dataset membership is assigned before expansion. The appearance model adds
15 optional SH residuals to the scalar opacity logit and activates them on the
same progressive degree schedule as color SH. This is the bounded
`opacity-SH-v1` subset of FAGK, not direction-dependent scale or rotation.
Checkpoint V4 stores the new Adam moments and intentionally rejects prior raw
Gaussian layouts.

The scope follows the opacity-only ablation described by
[TOrtho-Gaussian](https://doi.org/10.1080/10095020.2026.2622788): the base
opacity logit receives a view-direction SH residual before sigmoid activation.
The paper's fully anisotropic scale and rotation extensions remain future
experimental work and are not implied by the `opacity-SH-v1` name.

The real SH sign/order convention is a tested cross-language contract. The
native CPU implementation and the array-generic Python implementation used by
the CuPy renderer are evaluated on the same normalized directions through
`dronegs_sh_python_parity_tests`; native CUDA parity remains covered by the
CPU/CUDA rasterization suite.

## Stable boundary

```text
COLMAP dense workspace
        |
        v
DroneGS contract-v1 process
        |
        +--- checkpoint + manifest + canary + point_cloud.ply
            |
            v
existing DroneAI GaussianModel and ortho pipeline
```

The integration boundary is a subprocess. DroneAI does not link to trainer
internals.

## In scope

- COLMAP binary model loading for undistorted pinhole datasets.
- Sparse-point initialization of 3D Gaussians.
- CUDA differentiable Gaussian rasterization.
- L1 and DSSIM photometric loss.
- Adam with parameter-specific schedules and progressive spherical harmonics.
- A strategy interface with MRNF-compatible behavior.
- Checkpoint/resume, deterministic seeds, cancellation, metrics, and PLY export.
- Single-GPU resident blocks in projected-ground core/buffer partitions. The
  camera-footprint/crop and streamed-product stages remain gated work.

## Out of scope before 1.0

- GUI, editing, viewport, video, USD, mesh, SOG/SPZ, plugins, Python, and MCP.
- General distorted, fisheye, equirectangular, or dynamic-scene training.
- Replacing DroneAI's CuPy orthographic renderer.
- Multi-GPU distributed training.

## Technology choices

- C++23 control plane and CUDA 12.9 or newer.
- CMake and Ninja.
- Purpose-built structure-of-arrays buffers rather than a general tensor API.
- CTest-driven native unit, finite-difference, and convergence checks.
- JSON Lines progress events and a versioned JSON final manifest.
- Standard 3DGS PLY as the inference/export boundary.

Any adapted source is recorded in `GPL_COMPONENTS.md` with its exact upstream
revision, license, copyright, and local paths.

## Production training slice

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

The trainer includes growth, split, reproducible weighted-Gumbel sampling,
Sobel edge guidance, optimizer-profile telemetry, prune/replacement, noise,
decay, progressive SH and the MRNF prune/reuse/compaction lifecycle. External
exact-pair LPIPS complements the native PSNR/SSIM evaluator. Inputs remain
limited to SIMPLE_PINHOLE and PINHOLE cameras.

Production V1 uses the same held-out index rule as the frozen benchmark:
`scene_index % test_every == 0`. Those descriptors never enter the shuffled
training schedule. Initial and final passes report full-frame RGB PSNR and
Gaussian 11x11 SSIM with sigma 1.5 and valid padding. PSNR/SSIM reductions
remain on CUDA; optional final predictions are downloaded only for lossless
PPM export and external perceptual evaluation.

Custom/V2 qualification may instead use `spatial-block`. Camera centers are
derived from COLMAP `qvec/tvec`, projected to their two dominant spatial axes,
and a deterministic central block is held out. A configurable guard ring is
ignored rather than trained; manifests record all three populations.

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
and is now the production Gaussian training path.
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
per-view CSV artifact. The matching historical Albagnac control was executed
through the pinned GPL LichtFeld runtime image; no LichtFeld metric source was
copied into the original MIT CUDA implementation and that image is no longer
a build/runtime dependency.
Version 0.5.0-dev.14 reuses that separable SSIM forward in every ordered
training step and stores five derivative terms per valid window center.
One CUDA thread per input RGB sample gathers the at-most 121 overlapping
centers, avoiding atomics in the image-space DSSIM backward. The public
diagnostic returns the rendered image, transmittance, objective, and exact
trainer image gradient; a CPU oracle plus eight central finite differences
guard the implementation. The Albagnac result isolates DSSIM from topology:
SSIM improves by 0.004378 while PSNR is effectively unchanged, leaving MRNF
growth as the next controlled parity factor.
Version 0.5.0-dev.27 fixes native Ada code generation and reduces projected
workspace pressure. CMake now establishes `89-real;89-virtual` before CUDA
compiler detection. Projection writes 48-byte render records plus separate
source-indexed SH bases; CUB sorts only the compact records by the existing
depth/source key. Lightweight kernels reconstruct tile bounds from projected
center/radius. Rendering stays coalesced in depth order, while backward reads
SH bases through the recovered source index. This avoids the dev.26
large-value CUB device-link failure and removes 112 bytes of persistent
projected-depth capacity per reserved Gaussian.
Version 0.5.0-dev.28 tunes the two stable CUB radix sorts for Ada by selecting
Policy610 instead of the slower CUDA 12.8 Policy800 path, while retaining the
48-byte projected record and deterministic 64-bit depth/source key. Native
sm_89 compilation is capped at 64 registers to improve occupancy in the
projection, rendering, backward, and optimizer kernels. The override is
limited to builds that contain architecture 89.
Version 0.5.0-dev.29 batches both passes of ordered-alpha backward through
tile-local shared memory. Each block cooperatively loads one projected splat
and source index per thread, then all 256 pixels consume the batch in the
original front-to-back or back-to-front order. This removes up to 256
redundant global projected-record reads per tile contribution without changing
the blend equation, gradient equation, stable ordering, or public contract.
Version 0.5.0-dev.30 makes that generic batching the portable CUDA baseline.
It removes the internal Ada-only CUB Policy610 dispatch and 64-register
ceiling. Local builds use CMake `native` GPU detection; distributable builds
can emit real CUDA 12.9 cubins for Turing through Blackwell in one fat binary.
Stable radix ordering and the renderer contract remain unchanged.
Architectures introduced after CUDA 12.9 require rebuilding with a toolkit
that supports them.
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

Version 0.5.0-dev.17 isolates the pinned MRNF optimizer profile. Initial
Gaussian positions are reduced to three 10th-90th percentile axis widths; the
median width scales an exponential position schedule from `2e-5` to `2e-7`.
Scale decays from `0.007` to `0.005`; DC, opacity, and rotation remain constant
at `0.002`, `0.012`, and `0.002`, and Adam epsilon is `1e-15`. The learning
rates are observable through a read-only context diagnostic and JSON events.
On Albagnac this exact profile is a negative result: it reduces position LR
61.5x and DC LR 25x, then loses 0.9566 dB and 0.02603 SSIM versus dev.16 while
preserving population. Therefore optimizer values are not portable across the
two gradient/parameter conventions without effective-update calibration.

Version 0.5.0-dev.18 retains both optimizer profiles in the same binary and
samples approximately 4,096 Gaussians deterministically at the first step,
every fifth of the run, and the final step. For each parameter family it emits
incoming gradient RMS, actual applied update RMS after bounds/quaternion
normalization, resulting parameter RMS, and component count. The identical
Albagnac replay reproduces dev.16 at 17.07045 dB / 0.245493 SSIM and the
LichtFeld-absolute result at 16.11581 dB / 0.219508 SSIM. At the common first
step, gradients match but dev16's actual DC and position deltas are 20.35x and
58.24x larger; opacity is 0.677x. This rejects a global LR multiplier and makes
dev16 the default quality profile. The next calibration slice changes one
family at a time.

Version 0.5.0-dev.19 gives every parameter family an independent Adam epsilon
and adds exact DC-only, opacity-only, position-only, scale-only, and
rotation-only profiles. Each profile changes one family's complete optimizer
behavior while retaining dev16 for the other four. A same-binary 500-step
Albagnac control reaches 17.07198 dB / 0.245501 SSIM. Position-only falls to
15.94478 dB / 0.216094 and is the dominant cause of the direct-profile
regression. Opacity-only improves both aggregates to 17.08820 dB / 0.245784;
DC-only trades +0.08181 dB for -0.000269 SSIM; scale and rotation are neutral.
Dev16 remains the default pending a longer-budget opacity confirmation.

Version 0.5.0-dev.20 adds a strict DC-plus-opacity profile. On the identical
1,000-step Albagnac protocol, dev16 reaches 17.51403 dB / 0.251635 SSIM,
opacity-only reaches 17.52109 dB / 0.251892, and DC-plus-opacity reaches
17.63194 dB / 0.251785. The combination improves aggregate PSNR and SSIM but
regresses SSIM on 106/172 views, whereas opacity-only improves 130/172 SSIM
views. Dev16 therefore remains the default; the next slice should sweep
intermediate DC rates while keeping LichtFeld opacity and dev16 geometry.

Version 0.5.0-dev.21 adds DC=0.005, 0.010, and 0.020 calibration profiles with
LichtFeld opacity and otherwise unchanged dev16 behavior. The same-binary
1,000-step control reaches 17.51451 dB / 0.251634 SSIM. DC=0.010 reaches
17.66035 dB / 0.252962 and improves 168/172 PSNR and 143/172 SSIM views;
DC=0.020 reaches 17.63374 dB / 0.253027 and improves 171/172 PSNR and 161/172
SSIM views. The former is the primary balanced-quality candidate and the latter
the robust-view candidate. Neither replaces the default before second-scene
replication and LPIPS.

Version 0.5.0-dev.22 validates the unchanged dev.21 profiles on Savères. The
1,000-step same-binary control reaches 16.65243 dB / 0.131453 SSIM. DC=0.020
reaches 16.79856 dB / 0.132098 and wins 132/134 PSNR plus 103/134 SSIM views.
Across 306 held-out views on both scenes it averages +0.13101 dB /
+0.001065 SSIM and wins 303 PSNR plus 264 SSIM views. DC=0.020 is therefore
the recommended quality profile, while dev16 remains the default throughput
profile because the quality candidate is slower and LPIPS remains open.

The dev.15-dev.21 behavior was adapted after inspection of pinned LichtFeld GPL
sources. `cuda/rasterization.cu` and `cuda/trainer.cu` are consequently marked
GPL-3.0-or-later and recorded with exact upstream/local paths in
`GPL_COMPONENTS.md`. The remaining original DroneGS units retain their
existing MIT identifiers; the linked dev.15+ native binary, including dev.22,
is GPL-covered.

## Source layout

```text
app1-colmap/dronegs/
    CMakeLists.txt                  C++/CUDA targets and portable architecture preset
    include/dronegs/               CLI, model, training and manifest contracts
    src/                           CPU control plane, I/O, manifest and CLI
    cuda/                          loss, rasterization and trainer kernels
    tests/                         CPU and CUDA contract tests
    benchmarks/                    native rasterization benchmark
    schema/                        completed production-manifest schema
    tools/                         isolated LPIPS evaluator

app1-colmap/gaussian_training/
    backend adapter, identity/reuse checks and benchmark support
```

## Reproducibility

Run-manifest v1 records trainer, source, parameter, timing, metric and artifact
provenance. The native process records its version, Git revision, contract,
parameters, seed, dataset fingerprint, losses, timings and final Gaussian
count; its hardware and artifact-hash placeholders remain null. The Python
production boundary adds the trainer-binary and PLY SHA-256 identities before
accepting or reusing an artifact, while the benchmark harness records GPU,
driver and peak-VRAM telemetry. Benchmark seeds are mandatory. Dataset inputs
are read-only and outputs use a distinct run directory.

## Large-scene performance principles

- asynchronous decode and host-to-device transfer;
- bounded GPU image cache and double buffering;
- persistent allocations up to Gaussian capacity;
- fused loss and optimizer kernels;
- CUDA Graph capture between topology changes when profitable;
- spatial partitioning without loading all full-resolution images at once;
- separate startup, loading, training, checkpoint, filtering, and ortho timing.

Performance changes are accepted only at equal measured quality.
