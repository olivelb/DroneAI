# DroneGS implementation roadmap

Each completed phase has one focused commit and an annotated
`dronegs-vMAJOR.MINOR.PATCH` tag.

## Phase map

| Version | Phase | Deliverable | Exit gate |
|---|---|---|---|
| 0.1.0 | Foundation | Architecture, contracts, provenance | Documents and schema valid |
| 0.2.0 | Benchmark oracle | Repeatable trainer harness | Five-run summaries and immutable outputs |
| 0.3.0 | Backend boundary | LichtFeld and DroneGS adapters | Existing LichtFeld workflow unchanged |
| 0.4.0 | Native vertical slice | COLMAP load, fixed topology, PLY | Gradient and PLY compatibility tests |
| 0.5.0 | Differentiable trainer | Rasterizer, loss, Adam, SH | Fixed-topology parity |
| 0.6.0 | MRNF parity | Split/prune/grow, reuse, resume | Growth and convergence parity |
| 0.7.0 | Non-regression | Drone and public benchmark suite | Quality, speed, VRAM gates pass |
| 0.8.0 | Large-scene optimization | Streaming, cache, partition tuning | 1,000+ image workload bounded and faster |
| 0.9.0 | Canary | Shadow and selected production runs | No severity-1/2 regression |
| 1.0.0 | Sole backend | DroneGS checkpoints, canary and deployment | Operational acceptance |

## Current status

- Completed tagged phase: Phase 3.
- Current development version: 0.5.0-dev.57.
- Production Gaussian backend: DroneGS. No LichtFeld executable, adapter,
  image, checkout or vcpkg build remains in the pipeline.
- The Albagnac 15,000-step gate passed on the frozen 172-view evaluator:
  DroneGS reaches 22.175919 dB PSNR, 0.642557 SSIM and 0.325408 LPIPS in
  972.731 training seconds. Deterministic LichtFeld reaches 21.513821 dB,
  0.586497 SSIM and 0.371055 LPIPS in 994.228 seconds.
- The immutable production recipe uses the `reference-absolute` optimizer,
  structural FastGS rasterizer, spatial-bounds pruning, progressive SH3,
  a 1,000-step topology cooldown and a 1,000-step photometric finish ending
  at 100% MSE.
- The default image builds DroneGS locally with portable CUDA device code and
  ships matching source/provenance. No LichtFeld checkout is needed for the
  default pipeline.
- Versioned full-state checkpoint/resume and held-out PSNR/SSIM canary gates
  are implemented in dev.46.
- Dev.47 adds checksum-protected checkpoint V3, strict dataset/binary/PLY
  reuse identity, a native production-profile registry and optional
  spatial-block/guard-ring evaluation.
- Dev.48 implements source-image tile views and the scoped opacity-SH-v1
  appearance model, area-filtered image reduction, cross-language SH parity
  and a post-filter achieved-density GSD gate. Component/CUDA gates pass; a
  representative full-quality E2E comparison remains required before changing
  production quality claims.
- Dev.49 adds crop-camera projected initialization, exact capacity-targeted
  growth and topology/noise schedules derived from every operator-selected
  iteration budget. Fast-v2 passes its representative cell gate; Normal-v4,
  HQ-v4 and facade promotion retain their separate target-GPU gates.
- Dev.50 keeps loss and validation scalars on the GPU between the 20 progress
  reports of a run, while a device-side sticky validator preserves fail-closed
  handling of empty or non-finite training frames. The bounded raster path
  also rejects tile bounding boxes that cannot contain a contributing ellipse;
  structural FastGS deliberately retains its packed checkpoint layout.
- The independent Savères 15,000-step qualification passed on 1,065 images:
  19.163038 dB PSNR, 0.456047 SSIM, 0.551232 LPIPS, 1.5 million splats and
  40.93 minutes of training. Its PSNR/SSIM production canary passed.
- The final dev.47/V1 path was repeated over five complete seeds on the new
  1,066-image SAVERES RTK reconstruction: every run completed, median wall
  time was 607.1 seconds, median peak VRAM 2,124 MiB, mean PSNR 19.4122 dB and
  mean SSIM 0.49155 over 134 held-out views.
- The portable Turing-through-Blackwell image was rebuilt from the final
  source, started on the local NVIDIA GPU, and audited for matching source,
  notices, GPL text, and absence of LichtFeld/vcpkg runtime artifacts.
- Production V1 no longer has a trainer-parity gate. Publishing the immutable
  evidence archive is a release action; ALBAGNAC plus SAVERES spatial-block
  repetitions remain the separate gate for any V2 profile.

### Development history

The following entries retain the measured progression that led to production
V1 and dev.48.
- Phase 4 sub-gate completed: COLMAP projection, JPEG decode, differentiable
  additive splatting, DC/opacity Adam, synthetic convergence, and GAJAN smoke.
- Large-scene memory sub-gate completed: RGB8 targets, lazy 256 MiB LRU cache,
  cardinality stress test, and cache telemetry.
- Real large-scene gate completed on Albagnac: 1,376 images, 1,025,093 fixed
  Gaussians, 309 evictions, 267.3 MB peak image cache, and no CUDA OOM.
- Large-scene decode-overlap sub-gate completed: persistent one-slot prefetch,
  0.954 s median image wait, and 15.9% lower warm wall time than dev.2.
- Ordered-alpha correctness foundation completed: CPU reference renderer,
  transmittance contract, stable depth ordering, thresholds, and native tests.
- Tiled-alpha CUDA forward sub-gate completed: GPU projection, deterministic
  depth sorting, GPU tile-pair construction, 16x16 shared-memory rendering,
  and CPU/CUDA output parity.
- The GPU tile pipeline reduced a 1,025,093-splat / 800x580 end-to-end forward
  benchmark from 146.311 ms to 35.395 ms median (4.13x) versus dev.5.
- Ordered-alpha DC/opacity backward foundation completed: CPU reference,
  direct finite differences, tiled CUDA reverse composition, early-exit tests,
  and CPU/CUDA gradient parity.
- Forward+backward measured 52.889 ms at 1,025,093 splats and 800x580 in
  Release/sm_89, including per-call allocation and host readback.
- Persistent ordered-alpha training sub-gate completed: reusable CUDA/CUB/tile
  buffers, device-side L1 gradient, ordered backward, and Adam.
- The integrated Albagnac 500-iteration Release/sm_89 run completed in 25.80
  seconds with a
  651 MiB sampled total-VRAM delta and reduced anchor L1 by 22.6%.
- Bounded JPEG decode experiments completed. A depth-8 / two-worker queue made
  all 499 scheduled prefetches ready but regressed 500-iteration wall time by
  3.6% versus the same-cycle one-worker control on the RTX 4070 Laptop.
- Reduced-IDCT JPEG decode remains opt-in: it shortened the same-cycle
  500-iteration control by 2.1%, but changes filtered RGB targets and therefore
  cannot become the default before held-out quality validation.
- Anisotropic covariance forward completed: normalized Gaussian quaternion and
  non-uniform scale are transformed through the camera and perspective
  Jacobian into a bounded inverse 2D conic on CPU and CUDA.
- The anisotropic million-Gaussian benchmark measures 44.934 ms forward and
  64.416 ms forward+backward. The Albagnac 500-iteration run completed in
  30.316 seconds and reduced anchor L1 by 22.6% without OOM.
- Public anisotropic geometry backward now returns position, log-scale, and
  normalized-quaternion gradients. All ten geometry components pass direct
  finite differences on a branch-stable fixture.
- The million-Gaussian public-call medians are 40.528 ms forward and 91.601 ms
  forward+geometry-backward. Persistent geometry Adam was integrated in
  dev.12.
- Persistent geometry Adam is complete: gradients and moments remain on
  device, position uses a scene-scaled exponential schedule, log-scales are
  bounded, and rotations are renormalized.
- Albagnac 500-iteration anchor L1 reaches 0.104295, a 48.0% reduction from
  initialization and 32.8% below the dev.11 fixed-geometry result. Trainer
  compute is 39.552 seconds with an 838 MiB sampled total-VRAM delta.
- Deterministic held-out evaluation is complete: the explicit
  `scene_index % test_every == 0` split matches LichtFeld, is excluded from
  every training schedule, and reports GPU PSNR plus 11x11 valid SSIM.
- Albagnac reserves 172 of 1,376 views. DroneGS improves from 14.0631 to
  17.1212 dB and from 0.1811 to 0.2419 SSIM over 500 steps. The historical
  pinned LichtFeld report reaches 21.0686 dB and 0.6310 SSIM on a different
  172-view ordering; dev.38 supersedes that early cross-run comparison with
  direct same-model/same-camera/same-split PLY evaluation.
- Analytical DSSIM is complete. The atomics-free CUDA image gradient passes
  eight direct finite differences against the same CPU objective used by the
  trainer. On Albagnac it improves SSIM on 171 of 172 held-out views.
- The dev.14 mean is 17.1154 dB / 0.246278 SSIM. Versus dev.13 this is
  -0.0058 dB / +0.004378 SSIM at a 4.4% trainer-compute cost.
- MRNF contribution/error weighting, 200-step cadence, threshold, 7% growth,
  capacity reservation, and rotated long-axis split are integrated in dev.15.
- Albagnac grows from 1,025,093 to 1,173,576 Gaussians, only 36 above the
  pinned LichtFeld population. Quality nevertheless regresses dev.14 by
  0.0556 dB and 0.001321 SSIM; trainer compute rises from 41.052 to 55.865 s.
- Weighted Gumbel top-K and edge guidance are integrated in dev.16. Selection
  is deterministic for a CLI seed; Sobel edge contribution is accumulated in
  existing training renders, avoiding extra Canny/raster passes.
- Dev.16 reaches 17.0717 dB / 0.245508 SSIM and 1,173,573 Gaussians. It
  improves dev.15 by 0.01195 dB / 0.000550 SSIM, but trainer compute rises
  another 8.6% to 60.683 s.
- Exact MRNF optimizer constants and exponential position/scale schedules are
  integrated in dev.17. Albagnac's position LR falls 61.5x because the spatial
  normalization changes from the full bounding-box diagonal to the median
  10th-90th percentile axis width; DC LR falls 25x.
- Dev.17 reaches only 16.1151 dB / 0.219473 SSIM despite ending at 1,173,577
  Gaussians. It regresses dev.16 on 169/172 PSNR views and every SSIM view.
  Direct absolute-rate copying is rejected without gradient-scale calibration.
- Dev.18 keeps both profiles in one instrumented binary and reproduces the
  dev.16 anchor at 17.07045 dB / 0.245493 SSIM. The LichtFeld-absolute replay
  reaches 16.11581 dB / 0.219508 SSIM.
- Deterministic sampled telemetry shows nearly equal incoming gradient scales
  at the shared first step, but actual dev16 updates are 20.35x larger for DC
  and 58.24x larger for position. LichtFeld-absolute instead applies a 1.48x
  larger opacity update and substantially larger late scale/rotation updates.
- The accepted `dronegs-dev16` profile was restored as the then-current
  low-level CLI default.
- Dev.19 isolates all five parameter families, including per-family Adam
  epsilon, in the same binary. Position-only explains the dominant regression:
  it loses 1.1272 dB / 0.02941 SSIM and regresses every held-out SSIM view.
- Opacity-only is the first no-compromise candidate at +0.0162 dB /
  +0.000283 SSIM. DC-only gains +0.0818 dB but loses 0.000269 SSIM; scale and
  rotation are neutral at 500 steps.
- Dev.20 combines DC and opacity without changing the other families. At
  1,000 steps it improves the same-binary control by +0.11791 dB and
  +0.000150 mean SSIM, but 106/172 individual SSIM views regress.
- Opacity-only remains the most homogeneous candidate at 1,000 steps:
  +0.00706 dB / +0.000257 SSIM, with 130/172 SSIM views improving.
- Dev.21 sweeps intermediate DC rates `0.005`, `0.010`, and `0.020` with
  LichtFeld opacity while keeping dev16 geometry. At 500 steps, `0.010` has
  the best mean PSNR and `0.020` the best mean SSIM and per-view coverage.
- The 1,000-step confirmation selects DC=0.010 as the primary balanced-quality
  candidate: +0.14583 dB / +0.001328 versus the same-binary control, with
  168/172 PSNR and 143/172 SSIM views improving. DC=0.020 is the robust-view
  candidate at +0.11923 dB / +0.001393, with 171/172 PSNR and 161/172 SSIM
  views improving.
- Dev.22 replicates dev16, DC=0.010, and DC=0.020 on the independent Savères
  Mavic 3E RTK scene. At 1,000 steps, DC=0.020 improves the Savères control by
  +0.14613 dB / +0.000644 SSIM, with 132/134 PSNR and 103/134 SSIM views
  improving.
- Across 306 held-out views from Albagnac and Savères, DC=0.020 wins 303 PSNR
  and 264 SSIM comparisons, averaging +0.13101 dB / +0.001065 SSIM. It is the
  recommended quality profile. Dev16 remains the default throughput profile
  because DC=0.020 increases manifest wall time by 8.2% on Albagnac and 19.3%
  on Savères.
- Dev.23-dev.25 add exact-pair LPIPS, progressive degree-3 SH, and the complete
  MRNF prune/reuse/noise/decay/compaction lifecycle. Dev.26 validates those
  paths on GAJAN smoke, Savères, and Albagnac without rerunning COLMAP.
- Dev.27 restores native `sm_89` code generation by sorting compact 48-byte
  render records and storing SH bases separately. All six suites pass, and
  three-scene bounded PSNR/SSIM/LPIPS remains equivalent. Gajan improves
  training/wall time by 5.8%/15.5%; Savères is wall-neutral (+0.4%), while
  Albagnac remains 6.0% slower wall-clock than the dev.26 PTX-JIT control.
- Dev.29's generic tile-local shared batching reaches bounded wall-time gains
  of about 30% on Savères and 33% on Albagnac versus dev.26. Dev.30 removes
  the later Ada-only radix/register tuning and adds automatic local or
  portable Turing-through-Blackwell compilation; cross-architecture runtime
  validation remains required.
- Dev.31 replaces the uniform initial scale with deterministic exact local-KNN
  MRNF scales. On the pinned Albagnac 500-step control it improves SSIM by
  0.09006 and exact-pair LPIPS by 26.2%, cuts trainer compute by 89.1%, but
  loses 0.4198 dB PSNR. This exposes frozen high SH color and opacity/covariance
  mismatches as the next quality gates.
- Dev.32 adopts FastGS's `[0,4]` live SH color interval and recalibrates the
  post-KNN balanced DC rate to 0.010. Versus dev.31 it gains 0.2230 dB,
  0.00353 SSIM, and 1.62% LPIPS. Versus dev.30 it remains 0.1968 dB lower
  but improves SSIM by 0.09359, LPIPS by 27.4%, trainer compute by 87.3%,
  and wall time by 49.9%.
- Dev.32 generalizes on the three existing COLMAP scenes. GAJAN gains
  0.06216 SSIM and 12.4% LPIPS while halving both trainer and wall time.
  Savères at 1,000 steps reaches 17.5385 dB / 0.330330 SSIM / 0.798290
  LPIPS, exceeding the historical 1,000-step PSNR by 0.6998 dB and SSIM by
  0.19893 while reducing trainer compute by 84.5%.
- An isolated FastGS projected-covariance transplant was rejected. Although
  it improved the initial Albagnac render, the 500-step control collapsed to
  11.9919 dB / 0.193172 SSIM and took 2.67x the dev.32 trainer time.
  Covariance cannot be separated safely from FastGS bounds, overlap, and
  composition behavior; no candidate code was retained.
- Dev.33 calibrates opacity convergence after local-KNN initialization without
  changing initialization, renderer, geometry, topology, or architecture
  policy. The selected LR=0.096 profile improves PSNR, SSIM, and LPIPS on
  Albagnac, GAJAN, and Savères. Albagnac gains 0.9791 dB / 0.01602 SSIM /
  10.83% LPIPS; Savères gains 0.1397 dB / 0.00365 / 7.53% and finishes with
  2.4% fewer Gaussians.
- The Albagnac dev.33 1,000-step control reaches 18.7862 dB / 0.410530 SSIM /
  0.631623 LPIPS, confirming continued convergence. Its median scale already
  matches LichtFeld closely, but median anisotropy is only 1.082 versus 1.451
  and median rotation is 0.022 versus 0.347 rad. Scale-anisotropy and rotation
  calibration are now the dominant isolated optimizer gate.
- Dev.34 isolates scale and rotation. The combined profile improves PSNR and
  SSIM on Albagnac, GAJAN, and Savères and improves LPIPS on the first two,
  but Savères LPIPS regresses by 0.33%. It remains an opt-in structure profile;
  dev.33 remains the balanced recommendation.
- The quality/speed portion of the Phase 4 gate was closed by dev.45.
  Checkpoint/resume and broader downstream/cross-architecture qualification
  remain open.
- Pinned double-buffered host-to-device staging was benchmarked and rejected:
  measured upload service was only about 0.06 s per 500-iteration Albagnac run,
  while both tested orchestrations regressed median wall time.
- The next priority is checkpoint/resume plus a staged DroneGS canary on a
  second production-scale scene. Portable CUDA compilation remains the
  baseline; future throughput work must improve generic kernels rather than
  introduce per-architecture policy overrides. The combined approximately
  2,000-image Albagnac throughput run remains deferred while COLMAP bundle
  adjustment is unbounded on CPU.

## Versioning rules

1. A phase is tagged only after its automated checks pass.
2. The phase commit updates `VERSION` and `CHANGELOG.md`.
3. Benchmark reports record both the project version and exact Git SHA.
4. Contract-breaking changes create a new contract version.
5. Experimental commits are allowed, but only an exit-gate commit gets a tag.

## Provisional reference and gates

The clean pinned GAJAN Phase 3 suite provides a repeatable performance oracle,
but not yet a held-out image-quality baseline. Full details are in
`benchmarks/phase3-gajan-lichtfeld-2026-07-24.md`.

| Workload | Reference |
|---|---:|
| 111 images, LichtFeld median wall time (5 runs) | 89.785 s |
| LichtFeld wall-time range | 85.869-90.471 s |
| Iterations | 5,000 |
| Median splats before filtering | 284,418 |
| Median peak VRAM total-memory delta | 1,484 MiB |
| GPU | RTX 4070 Laptop, 8 GiB |

| Metric | Non-regression gate |
|---|---:|
| Held-out PSNR | reference - 0.10 dB maximum |
| Held-out SSIM | reference - 0.002 maximum |
| Held-out LPIPS | reference + 0.005 maximum |
| Median trainer time | reference + 3% maximum |
| Peak VRAM | no increase |
| Orthomosaic useful coverage | no regression |
| Labelled downstream metric | no regression |

Large-scene targets are added after selecting at least one representative
dataset with 1,000 or more images. Reports normalize throughput by image count,
source pixels, iterations, and Gaussian count.

## Go/no-go reviews

- After 0.4.0: rasterizer maintainability.
- After 0.6.0: reachable quality parity.
- After 0.7.0: economic value of further optimization.
- Before 1.0.0: licensing, source obligations, disaster recovery, and
  reproducibility.
