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
- Current development version: 0.5.0-dev.6.
- Production backend: LichtFeld.
- DroneGS native backend: experimental fixed-topology additive trainer; opt-in only.
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
- Phase 4 exit gate still open: ordered-alpha training integration and backward,
  geometry/scale/rotation gradients, DSSIM, progressive SH, held-out quality
  metrics, and LichtFeld parity.
- Pinned double-buffered host-to-device staging was benchmarked and rejected:
  measured upload service was only about 0.06 s per 500-iteration Albagnac run,
  while both tested orchestrations regressed median wall time.
- The immediate Phase 4 priority is the ordered-alpha backward pass, followed by
  anisotropic covariance and measured held-out quality parity. Further
  performance work must be selected from a GPU kernel profile.

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
