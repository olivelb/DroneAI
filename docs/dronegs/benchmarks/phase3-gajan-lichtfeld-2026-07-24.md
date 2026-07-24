# Phase 3 GAJAN baseline

Date: 2026-07-24
Status: accepted LichtFeld timing and memory baseline

## Environment

| Field | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU, 8,188 MiB |
| Driver | 610.62 |
| LichtFeld image | `sha256:71913f535a208879b9cd2e84f17895849c51de53e457149bd12c85c95e44568f` |
| CUDA runtime image | 12.8.1 |
| Dataset | `gajan-r2s-full/dense` |
| Images | 111 |
| Image bytes | 435,155,768 |
| Dataset fingerprint | `b2cb5d2397296fb95bd164c0be4bb3aa79c3897fa1150587c577ea263d7be328` |
| Iterations | 5,000 |
| Strategy | MRNF |
| SH degree | 1 |
| Maximum Gaussians | 500,000 |
| Resize factor | 4 |
| Maximum width | 1,600 |
| Tile mode | 4 |

The exact report remains outside Git at:

```text
/home/olivier/droneAI-workspaces/benchmarks/phase3-gajan-clean/
  gajan-v1-docker/benchmark_summary.json
```

## Five clean runs

| Run | Wall time | Peak VRAM delta | Final splats |
|---:|---:|---:|---:|
| 1 | 86.534 s | 1,474 MiB | 283,961 |
| 2 | 85.869 s | 1,488 MiB | 283,894 |
| 3 | 90.471 s | 1,490 MiB | 284,418 |
| 4 | 89.785 s | 1,484 MiB | 284,442 |
| 5 | 90.206 s | 1,471 MiB | 284,458 |

| Aggregate | Wall time | Peak VRAM delta | Final splats |
|---|---:|---:|---:|
| Minimum | 85.869 s | 1,471 MiB | 283,894 |
| Median | 89.785 s | 1,484 MiB | 284,418 |
| P95 / maximum | 90.471 s | 1,490 MiB | 284,458 |

VRAM is the peak increase in total GPU memory above the pre-run baseline. This
fallback is used because the process visible to the harness is the Docker
client, while the CUDA process runs inside the container.

The requested repetition seeds are recorded but are not effective: the pinned
LichtFeld CLI exposes no verified global seed option. The resulting splat count
variation is therefore expected and explicitly retained in the oracle.

An earlier five-run attempt is excluded because its first repetition briefly
overlapped an accidentally detached training process. No excluded measurement
is used in the aggregate above.

## Native vertical-slice smoke result

The original DroneGS 0.4 fixed-topology executable was also run against the
25-image GAJAN smoke dataset:

| Metric | Result |
|---|---:|
| Sparse points / initialized Gaussians | 9,324 |
| COLMAP load | 5.55 ms |
| PLY export | 3.18 ms |
| Native wall time | 9.16 ms |
| PLY bytes | 858,427 |

This is an I/O and compatibility measurement, not a training performance or
quality comparison. No photometric optimization exists in version 0.4.

## Phase gate

Phase 3 passes its COLMAP, PLY compatibility, manifest, and gradient gates. The
go/no-go review is **GO** for a bounded differentiable rasterizer prototype in
Phase 4. This decision does not assert training quality or speed parity; those
remain gated by the later fixed-topology and MRNF benchmark phases.
