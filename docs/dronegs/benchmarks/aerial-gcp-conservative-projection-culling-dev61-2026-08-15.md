# Aerial GCP conservative projection culling — 2026-08-15

## Verdict

DroneGS dev.61 passes the repeated fixed-topology 5.1 M-Gaussian short gate.
Mean native training falls from 39.034 seconds in dev.60 to 37.561 seconds
(`-3.77%`), while loss, PSNR, SSIM and population remain equivalent. Mean
wall time falls from 59.658 to 58.526 seconds (`-1.90%`).

The optimization is deliberately stateless. Position, rotation and scale
continue to change after topology refinement, so a persistent per-view
visibility cache would require a difficult invalidation contract. Dev.61
instead derives an outward-rounded maximum screen-space support on every
projection. Only splats that cannot overlap the image even at opacity one are
rejected before SH evaluation and exact covariance projection. All uncertain
cases continue through the unchanged exact path.

## Controlled setup

| Item | dev.60 reference | dev.61 candidate |
| --- | --- | --- |
| Host / GPU | BIGZEN / RTX 3090 24 GiB | same |
| Build | Release, CUDA 12.0, `sm_86` | same |
| Candidate binary SHA-256 | — | `5bf39081c5c0c0176f3ad0c8fde370d0c7dbdeb5c3c1e50ae502a3433edfa02b` |
| Dataset fingerprint | `fnv1a64:v3:b75a413130a5daa2` | same |
| Training / held-out views | 41 / 5 | same |
| Iterations / population | 1,000 / 5,100,000 fixed | same |
| Optimizer / raster | AbsGrad 0.50 / FastGS | same |
| SH / seed | SH3 / 42 | same |
| Checkpoint / topology | disabled / fixed | same |

The candidate Release build passed all eight native CPU/CUDA CTests. Both
candidate processes exited with code zero, without OOM or non-finite error.
The short-gate executable was built before the version string was advanced,
so its manifest still identifies dev.60; its rasterization source is the
dev.61 implementation recorded by this report.

## Repeated runtime and quality

| Version | Mean wall | Mean training | Mean loss | Mean PSNR | Mean SSIM |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev.60 | 59.658 s | 39.034 s | 0.02308726 | 21.85816 dB | 0.481299 |
| dev.61 | 58.526 s | 37.561 s | 0.02308794 | 21.86207 dB | 0.481445 |

| Delta dev.61 vs dev.60 | Training | Wall | PSNR | SSIM |
| --- | ---: | ---: | ---: | ---: |
| Mean of two runs | **-3.77%** | **-1.90%** | +0.00391 dB | +0.000146 |

Pixel-weighted PSNR changes from 21.16545 to 21.17042 dB and pixel-weighted
SSIM from 0.498786 to 0.498955. Both runs retain exactly 5.1 M Gaussians.
At sampled iteration 999, dev.61 projection is about 4.05–4.11 ms and the
complete GPU step about 38.32–38.50 ms. The remaining dominant fixed-topology
cost is the optimizer, not projection.

## Exact-commit 30,000-step HQ gate

The exact commit `42ff1a58f9ccd0ac153f42ed97398045b98b8723` was cloned
into a clean BIGZEN checkout, built in Release mode and passed all eight
native CPU/CUDA CTests. Binary SHA-256 is
`43b9765214a4b4b7ad4e28746e7c8cfe78646b7f24a626332195caed3a2b68de`.

The full representative HQ schedule completed with projected-KNN
initialization, capacity-targeted refinement through iteration 14,800, SH3,
1,000 fixed-topology cooldown iterations, a 1,000-step MSE finish and exactly
three checkpoints. It reached 5.1 M Gaussians and exited with code zero,
without OOM, non-finite error or temporary checkpoint file.

| Metric | dev.60 HQ | dev.61 HQ | Delta |
| --- | ---: | ---: | ---: |
| Native training | 941.939 s | 918.516 s | **-2.49%** |
| Wall time | 953.345 s | 930.127 s | **-2.44%** |
| Final loss | 0.0227742 | 0.0229636 | +0.0001894 |
| Mean PSNR | 21.8675 dB | 21.8484 dB | -0.0191 dB |
| Pixel-weighted PSNR | 21.1768 dB | 21.1649 dB | -0.0119 dB |
| Mean SSIM | 0.479513 | 0.479068 | -0.000445 |
| Pixel-weighted SSIM | 0.497511 | 0.497613 | +0.000102 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Checkpoints | 3 | 3 | 0 |

The PSNR delta is small and the SSIM delta remains comfortably inside the
established 0.002 long-run non-regression envelope. Visual inspection of a
representative held-out prediction found no missing support, clipping or new
boundary artifact; the dev.60 and dev.61 views are visually equivalent.

## Safety argument

For the bounded raster profile, projected covariance eigenvalues are already
clamped, so the maximum diagonal radius follows directly from the clamp and
antialias variance. For FastGS, dev.61 bounds each projected covariance row
using the maximum Gaussian axis, the camera-Jacobian row L1 norm and an
upward-rounded `sqrt(3)` factor. Its support uses opacity one and an
upward-rounded `sqrt(2 log(255))`, plus a one-pixel boundary margin. A
non-finite bound is treated as uncertain and therefore cannot cull.

The CUDA regression includes splats whose centers lie outside the image while
their exact projected support still overlaps it, alongside small remote splats
that are certainly invisible. CPU/CUDA visible counts and pixels must remain
identical.

## Retained evidence

The candidate remains on BIGZEN under
`/home/olivier/benchmarks/dronegs-conservative-projection-culling-dev61-20260815`.
The exact-commit HQ evidence remains under
`/home/olivier/benchmarks/aerial-gcp-hq-conservative-projection-culling-dev61-42ff1a5-20260815/cell0-r2`.
The dev.60 reference remains under
`/home/olivier/benchmarks/dronegs-fused-fastgs-sh-adam-dev60-20260814`.
No evidence was deleted.

| Candidate run | Manifest SHA-256 | Metrics SHA-256 |
| --- | --- | --- |
| A | `22c7f1f359aacfaaf0984d7fae20830e58f265af3af3e879019ef6f4d29da669` | `1674765fe23ed4134737327db085e3385f9174db64a4c426b811eaa02f0b7513` |
| B | `6a492842ca1f3a7db9d74763d7848a89ae1f58139735271c51557e02db29f7b5` | `471c5dcfb3847b2ea581c3545cfdbec43fa1d1c3261631b99157c31af2a49835` |

| Exact-commit HQ artifact | Size | SHA-256 |
| --- | ---: | --- |
| `point_cloud.ply` | 1,509,602,008 B | `43feae4adfa91daaa3dc5b9a81584673ee1f86c382d10f53bb4bac5b2547682d` |
| `training.ckpt` | 4,630,800,710 B | `333928ffe3e7ea2a7f9ef93a77273a130c0e02226b253358e375a785a67e56f9` |
| `trainer_run.json` | 7,285 B | `72c239b9699bb239dfdb83a05feb59bccd73c73381496db87d5335403b858c9a` |
| `evaluation/metrics.csv` | 1,384 B | `e778176b8ed19a01b50338e7ec2a614b50d9f93e5cea60355f8260bf7fbee182` |

Dev.61 passes its trainer promotion gate. Full-scene multi-cell aggregation,
raster seams, density, CRS and GSD remain independent product gates.
