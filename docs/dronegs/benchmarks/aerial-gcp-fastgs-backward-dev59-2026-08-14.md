# Aerial GCP FastGS backward qualification — 2026-08-14

## Verdict

The dev.59 active-only appearance-gradient expansion passes the representative
5.1 M-Gaussian cell gate. Across two fixed-topology 1,000-iteration runs on
the same RTX 3090, mean native training falls from 46.848 to 42.311 seconds
(`-9.69%`) and aggregate wall time falls from 134.308 to 124.701 seconds
(`-7.15%`). The sampled raster backward at iteration 999 falls from 14.664 to
9.485 ms (`-35.3%`). Loss, held-out PSNR/SSIM and population remain
equivalent.

The optimization is accepted for the structural FastGS path. It changes the
reduction layout, not the derivative equations: each source/tile now atomically
accumulates four raw appearance derivatives, then one thread per active
Gaussian expands the DC, color-SH and opacity-SH basis products. Exact-zero
appearance gradients return before coefficient writes, so invisible
Gaussians do not pay the expansion cost. Geometry and refinement-statistic
derivatives keep their original reduction.

## Controlled setup

| Item | dev.58 reference | dev.59 candidate |
| --- | --- | --- |
| Host / GPU | BIGZEN / RTX 3090 24 GiB | same |
| Build | Release, CUDA 12.0, `sm_86` | same |
| Candidate binary SHA-256 | — | `b705a742ce0028604d53ba1946e38fa17137318a32f7f8616122f8dbb73bae20` |
| Dataset fingerprint | `fnv1a64:v3:b75a413130a5daa2` | same |
| Training / held-out views | 41 / 5 | same |
| Iterations / population | 1,000 / 5,100,000 fixed | same |
| Optimizer | `reference-absolute-absgrad050` | same |
| Rasterizer / SH | structural FastGS / SH3 | same |
| Checkpoint / topology | disabled / fixed | same |

The candidate Release build passed all eight native CPU/CUDA CTests. The
training test explicitly exercises degree-three FastGS appearance updates and
requires an invisible Gaussian's DC, color-SH, opacity and opacity-SH values to
remain unchanged. Both benchmark processes exited normally without OOM. The
pre-commit binary manifest reports the qualification checkout as dirty; its
native source contents are the dev.59 implementation recorded with this
report.

## Repeated runtime and quality

| Run | Wall | Native training | Loss | PSNR | SSIM |
| --- | ---: | ---: | ---: | ---: | ---: |
| Reference A | 66.881 s | 46.810 s | 0.0230857 | 21.8634 dB | 0.481326 |
| Reference B | 67.116 s | 46.887 s | 0.0230936 | 21.8615 dB | 0.481473 |
| Candidate A | 62.258 s | 42.283 s | 0.0230840 | 21.8588 dB | 0.481303 |
| Candidate B | 62.145 s | 42.339 s | 0.0230870 | 21.8606 dB | 0.481511 |

| Aggregate metric | Reference | Candidate | Delta |
| --- | ---: | ---: | ---: |
| Mean training | 46.848 s | 42.311 s | **-9.69%** |
| Two-run wall | 134.308 s | 124.701 s | **-7.15%** |
| Mean PSNR | 21.86243 dB | 21.85970 dB | -0.00273 dB |
| Mean SSIM | 0.481399 | 0.481407 | +0.000007 |
| Mean pixel-weighted PSNR | 21.16881 dB | 21.16806 dB | -0.00075 dB |
| Mean pixel-weighted SSIM | 0.498905 | 0.498901 | -0.000005 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |

The tiny metric differences are well inside independent floating-point atomic
reduction noise. At sampled iteration 999, preprocessing is effectively
unchanged (6.670 versus 6.617 ms), while raster backward improves from 14.664
to 9.485 ms and total GPU-step time improves from 48.296 to 43.106 ms.

## Retained evidence

Reference runs remain on BIGZEN under
`/home/olivier/benchmarks/dronegs-single-gpu-cell-concurrency-dev58-20260814`.
Candidate runs remain under
`/home/olivier/benchmarks/dronegs-raster-backward-active-expansion-dev59-20260814`.
No evidence was deleted.

| Run | Manifest SHA-256 | Metrics SHA-256 |
| --- | --- | --- |
| Reference A | `adbfc49789fb3155e01e3ba5eaacb41a0d447dee8caca9147f9cb475d5c6e4e4` | `b612297239a64ceef684398a695a4fc24e82e0b0eb579731628284f0a8401434` |
| Reference B | `89b981e5659d5d2759eb46c738ebee63ba6bcf13fa4f69a24bdf1aff280952d7` | `ea14a5f5ba30f8f84ffc8fa0f5b1bf8cd8a492328e350480debd4f16e4b8efbb` |
| Candidate A | `97a5ed9092b4a3125c3f14f839817ec7ac140961c4995ff719ea7d65f016a12b` | `e43a8e17f491a1c7807b846f471be474bf636243d0c697fe3dec64322a709819` |
| Candidate B | `da2bccd8e250cbd7f80cd7ffda9a29028d6e85007d328ad33887444cc96651c0` | `9b31295a9bce8dc3a2e4026c1c07bc0cdf8b16b542daf6b1c2264066192bee53` |

This is a trainer hot-path acceptance gate, not a full-scene product gate.
Normal/HQ multi-cell aggregation, raster seams, density, CRS and GSD remain
covered by their independent E2E qualification.
