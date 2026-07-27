# Phase 4 Albagnac large-scene development result

Date: 2026-07-25
Status: real 1,000+ photograph scaling gate; not a LichtFeld parity result

## Dataset and preparation

| Item | Value |
|---|---:|
| Platform | DJI Mavic 3E RTK |
| Flight | Albagnac Oblique8 |
| Source photographs | 1,376 |
| Source payload | 17,035,485,184 bytes |
| Original dimensions | 5,280 x 3,956 |
| Undistorted dimensions | 3,200 x 2,320 PINHOLE |
| Training dimensions | 800 x 580 |
| Sparse registration | 1,376 / 1,376 (100%) |
| Sparse points / initial Gaussians | 1,025,093 |
| Sparse median reprojection error | 1.0761 px |

COLMAP produced 1,025,093 sparse points. RTK alignment used all 1,376 camera
references and reached a 0.188 m horizontal median residual, 0.409 m horizontal
P95, 0.255 m three-dimensional median, and 0.743 m maximum residual.

## DroneGS workload

| Item | Value |
|---|---:|
| Version | `0.5.0-dev.2` |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU, 8 GiB |
| CUDA image | NVIDIA CUDA 12.8.1 |
| Iterations / seed | 500 / 42 |
| Gaussian cap | 1,500,000 |
| Host image cache | 268,435,456 bytes |
| Artifact | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev2-500-fullcap/` |

The initial 500,000-Gaussian cap was rejected before allocation because the
sparse model contained 1,025,093 points. The successful run preserved the full
sparse topology with a 1.5 million cap.

## Result

| Metric | Value |
|---|---:|
| Initial anchor L1 | 0.1094132 |
| Final anchor L1 | 0.0929109 |
| Relative anchor improvement | 15.1% |
| Startup | 181.515 s |
| Synchronous image loading | 26.481 s |
| Training loop | 39.126 s |
| Checkpoint | 0.205 s |
| Wall time | 247.364 s |
| Cache requests | 502 |
| Cache hits / misses | 1 / 501 |
| Cache evictions | 309 |
| Peak decoded image cache | 267,264,000 bytes |

At 800 x 580 RGB8, one cached target occupies 1,392,000 bytes and the measured
peak is exactly 192 images. Eager storage of every target would require
1,915,392,000 bytes in RGB8 or 7,661,568,000 bytes in float32. The bounded cache
therefore removes 86.0% of eager RGB8 residency and 96.5% of the former float32
residency while processing a dataset over fifty times larger than GAJAN-25.

The manifest validates against `trainer-run-v1.schema.json`. The output PLY is
55 MiB and has SHA-256:

`6455cd5530a3226ce4b417116b0fcd993ea9c719a061bf923f51186f849b597f`

## Decision

The real large-scene memory gate passes:

- all 1,376 cameras and 1,025,093 fixed Gaussians load successfully;
- decoded target residency remains below the 256 MiB byte capacity;
- eviction works under real random access;
- training completes without CUDA OOM;
- anchor loss decreases at the million-Gaussian scale.

The result does not establish image-quality or LichtFeld parity. The current
rasterizer is additive, topology is fixed, only DC color and opacity are
optimized, and PSNR/SSIM/LPIPS are not implemented. The next engineering gate
remains asynchronous decode/prefetch and pinned transfers, followed by ordered
alpha compositing and held-out quality metrics.

No `dronegs-v0.5.0` tag is created from this result.
