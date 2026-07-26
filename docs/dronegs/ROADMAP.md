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
| 1.0.0 | Default backend | DroneGS default, LichtFeld rollback | Operational acceptance |

## Current status

- Completed tagged phase: Phase 3.
- Current development version: 0.5.0-dev.24.
- Production backend: LichtFeld.
- DroneGS native backend: experimental anisotropic ordered-alpha trainer with
  reproducible weighted-Gumbel MRNF growth, edge guidance, and held-out
  PSNR/SSIM and exact-pair external LPIPS; opt-in only.
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
  17.1212 dB and from 0.1811 to 0.2419 SSIM over 500 steps, but the pinned
  LichtFeld control reaches 21.0686 dB and 0.6310 SSIM on the same split.
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
- The accepted `dronegs-dev16` profile is restored as the default.
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
- Phase 4 exit gate remains open: the accepted quality anchor remains dev.16,
  while dev.17-dev.22 retain the optimizer experiments and candidates. LPIPS,
  prune/replacement/noise/decay and parity remain open; progressive SH is
  implemented.
- Pinned double-buffered host-to-device staging was benchmarked and rejected:
  measured upload service was only about 0.06 s per 500-iteration Albagnac run,
  while both tested orchestrations regressed median wall time.
- The immediate Phase 4 priority is to add LPIPS, then benchmark the recommended
  DC=0.020 quality profile and dev16 throughput default on the combined
  approximately 2,000-image Albagnac acquisition. Position must remain on the
  dev16 normalization/schedule.
  A future instrumented LichtFeld control would improve equivalence
  calibration. Progressive SH should follow the optimizer replication. The
  edge implementation also remains a candidate for fusion or
  refinement-window-only accumulation.
  For large-scene throughput, JPEG service remains material, but deeper and
  parallel CPU queues are now rejected on the current laptop. The next
  throughput candidate should reduce decoder work without changing targets,
  or move decode to a separately quality-gated GPU path.

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
- Before 1.0.0: licensing, source obligations, rollback, and reproducibility.
