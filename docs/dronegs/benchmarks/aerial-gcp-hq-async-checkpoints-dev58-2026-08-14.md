# Aerial GCP HQ asynchronous-checkpoint qualification — 2026-08-14

## Verdict

DroneGS dev.58 passes the representative HQ cell-0 gate. The standard 30,000
iteration run emits exactly three recovery checkpoints, keeps at most one
immutable snapshot in flight and preserves held-out quality. Against the
same-data dev.57 Release reference, wall time falls from 1,315.86 s to
1,105.67 s (**-15.97%**) and checkpoint-induced blocking falls from 225.68 s
to 28.25 s (**-87.48%**).

Mean PSNR changes by only -0.0018 dB and mean SSIM improves by 0.00256. The
5.1 M Gaussian ceiling, five held-out views and all scientific parameters are
unchanged. This accepts the checkpoint implementation as an operational
optimization; it does not change the HQ scientific profile.

## Controlled setup

| Item | dev.57 reference | dev.58 candidate |
| --- | --- | --- |
| Host / GPU | BIGZEN / RTX 3090 24 GiB | same |
| Build | Release, CUDA 12.0, `sm_86` | same |
| Native binary SHA-256 | `4d17b2b3b8df669ac696a99f78695e9675204b73c9f7ea9ca6d0acafbc04f62f` | `a990f86dbd9ecd68f685c7cb204f3e194f55777ebcc65e67cf86821ad82bf435` |
| Candidate source | — | commit `047f82d` |
| Dataset fingerprint | `fnv1a64:v3:b75a413130a5daa2` | same |
| Training / held-out views | 41 / 5 | same |
| Iterations / ceiling | 30,000 / 5.1 M | same |
| Optimizer | `reference-absolute-absgrad050` | same |
| Checkpoint cadence | every 2,000 iterations | every 10,000 iterations |

The candidate Release build passed all eight CPU/CUDA CTests before the long
run. It ran from `2026-08-14T20:26:06Z` to `2026-08-14T20:44:31Z`, completed
without OOM or runtime error and left no temporary checkpoint file. The native
manifest records the pre-commit checkout as `87a4119-dirty`; the built native
sources are the exact contents committed as `047f82d`.

An earlier functional replay used an unoptimized CMake build. It validated the
same checkpoint integrity and quality behavior but is intentionally excluded
from every speed comparison.

## Quality and runtime

| Metric | dev.57 reference | dev.58 candidate | Delta |
| --- | ---: | ---: | ---: |
| Final loss | 0.0226593 | 0.0227991 | +0.000140 |
| Mean PSNR | 21.8530 dB | 21.8513 dB | -0.0018 dB |
| Pixel-weighted PSNR | 21.1750 dB | 21.1685 dB | -0.0064 dB |
| Mean SSIM | 0.478160 | 0.480723 | **+0.002562** |
| Pixel-weighted SSIM | 0.496647 | 0.498635 | **+0.001988** |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Native training | 1,297.10 s | 1,094.57 s | **-15.61%** |
| Wall time | 1,315.86 s | 1,105.67 s | **-15.97%** |
| Topology refinement | 115.81 s | 113.48 s | -2.01% |
| Blocking checkpoint time | 225.68 s | 28.25 s | **-87.48%** |

| Held-out view | Reference PSNR | Candidate PSNR | Reference SSIM | Candidate SSIM |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 23.7709 | 23.7721 | 0.56805 | 0.57009 |
| 1 | 19.7824 | 19.7617 | 0.37452 | 0.37490 |
| 2 | 23.8835 | 23.8342 | 0.56578 | 0.57046 |
| 3 | 19.2984 | 19.3114 | 0.47114 | 0.47115 |
| 4 | 22.5298 | 22.5768 | 0.41131 | 0.41701 |

The small per-view differences are inside the established non-regression
envelope. FastGS uses floating-point atomic reductions, so byte-identical PLY
output is not an acceptance requirement across independently built binaries.

## Checkpoint behavior

| Iteration | Gaussians | Snapshot | Background write | Completion wait |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 1,297,897 | 0.646 s | 5.236 s | 0.076 s |
| 20,000 | 5,100,000 | 2.929 s | 21.310 s | 0.267 s |
| 30,000 | 5,100,000 | 2.937 s | 21.108 s | 21.393 s |

The first two writes overlap training almost completely. The final write is
correctly joined before evaluation and process completion. Aggregate capture
time is 6.51 s, aggregate background-write time is 47.65 s, and aggregate
completion wait is 21.74 s. Only one final `training.ckpt` is retained because
publication atomically replaces the previous recovery point.

## Retained evidence

The authoritative candidate remains on BIGZEN under
`/home/olivier/benchmarks/aerial-gcp-hq-async-checkpoint-dev58-release-r5-20260814/cell0`.
The reference remains under
`/home/olivier/benchmarks/aerial-gcp-hq-absgrad050-main6c18c9e-20260814/cell0`.

| Candidate artifact | Size | SHA-256 |
| --- | ---: | --- |
| `point_cloud.ply` | 1,509,602,008 B | `ea4dd46523faac9365d44b64984cba6ecf5db3295ca7a734a6c1c7715bbbf8c5` |
| `training.ckpt` | 4,630,800,710 B | `825b4a9b7017dda6138ce5db190fc952a83ad69298372e37e1811cf67b5df769` |
| `trainer_run.json` | 7,247 B | `1f7af28ee734d377414b0dbb65b44336d21a6dba7025a1ac86fbc49e98541956` |
| `evaluation/metrics.csv` | 1,384 B | `0f98a02bceb364ffb1463eca1f1a4eae123c0fe137dbfbadf45cc83947352e59` |

No evidence was deleted. The next independent performance gate is the FastGS
raster backward atomic-contention reduction.
