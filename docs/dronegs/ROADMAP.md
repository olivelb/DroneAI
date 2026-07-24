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

- Current phase: Phase 0.
- Current version: 0.1.0.
- Production backend: LichtFeld.
- DroneGS backend: not implemented.

## Versioning rules

1. A phase is tagged only after its automated checks pass.
2. The phase commit updates `VERSION` and `CHANGELOG.md`.
3. Benchmark reports record both the project version and exact Git SHA.
4. Contract-breaking changes create a new contract version.
5. Experimental commits are allowed, but only an exit-gate commit gets a tag.

## Provisional reference and gates

GAJAN currently provides integration reference numbers, not a statistical
quality baseline:

| Workload | Reference |
|---|---:|
| 111 images, LichtFeld training | 61.6 s |
| 111 images, total Gaussian runner | 92.0 s |
| Iterations | 5,000 |
| Splats before filtering | 284,448 |
| GPU | RTX 4070 Laptop, 8 GiB |

Before Phase 6, every reference workload is repeated at least five times with
a pinned image, driver, GPU power profile, seed, and dataset fingerprint.

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
