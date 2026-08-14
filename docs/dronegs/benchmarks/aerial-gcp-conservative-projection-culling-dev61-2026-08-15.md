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
The dev.60 reference remains under
`/home/olivier/benchmarks/dronegs-fused-fastgs-sh-adam-dev60-20260814`.
No evidence was deleted.

| Candidate run | Manifest SHA-256 | Metrics SHA-256 |
| --- | --- | --- |
| A | `22c7f1f359aacfaaf0984d7fae20830e58f265af3af3e879019ef6f4d29da669` | `1674765fe23ed4134737327db085e3385f9174db64a4c426b811eaa02f0b7513` |
| B | `6a492842ca1f3a7db9d74763d7848a89ae1f58139735271c51557e02db29f7b5` | `471c5dcfb3847b2ea581c3545cfdbec43fa1d1c3261631b99157c31af2a49835` |

An exact-commit 30,000-step HQ gate is required before promotion because this
change modifies the CUDA projection path.
