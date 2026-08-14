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
| Short-gate binary SHA-256 | — | `b705a742ce0028604d53ba1946e38fa17137318a32f7f8616122f8dbb73bae20` |
| Exact-commit HQ binary SHA-256 | — | `7e639d0f389b6acadc0abbf8b31fc06722fc3a9ecc726065ccd1b87826a767c8` |
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
report. The 30,000-step follow-up was built from a clean archive of commit
`dde086c`; archives intentionally contain no `.git`, so its native manifest
records revision `unknown` while the source directory, binary hash and commit
are retained together.

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

## Exact-commit 30,000-step HQ gate

The exact dev.59 commit then completed the full representative HQ schedule:
projected-KNN initialization, capacity-targeted topology through iteration
14,800, 1,000-step fixed-topology cooldown, 1,000-step MSE finish, SH3 and
three checkpoints. It reached 5.1 M Gaussians and completed without OOM,
temporary checkpoint file or non-finite error.

| Metric | dev.58 HQ | dev.59 HQ | Delta |
| --- | ---: | ---: | ---: |
| Native training | 1,094.575 s | 1,010.394 s | **-7.69%** |
| Wall time | 1,105.671 s | 1,021.230 s | **-7.64%** |
| Final loss | 0.0227991 | 0.0225331 | -0.0002660 |
| Mean PSNR | 21.8513 dB | 21.8438 dB | -0.0074 dB |
| Pixel-weighted PSNR | 21.1685 dB | 21.1793 dB | +0.0107 dB |
| Mean SSIM | 0.480723 | 0.476867 | -0.003855 |
| Pixel-weighted SSIM | 0.498635 | 0.497347 | -0.001288 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Checkpoints | 3 | 3 | 0 |

The long runs diverge slightly in topology because the changed floating-point
reduction order perturbs later prune/grow selection. Dev.59 remains within the
retained dev.57–dev.58 AbsGrad run envelope: its mean SSIM is 0.00129 below
the clean dev.57 replay, while PSNR differs by less than 0.01 dB from dev.58.
The paired fixed-topology gate above is the strict derivative/non-regression
comparison and shows no measurable aggregate quality loss.

The three dev.59 snapshots were emitted at iterations 10,000, 20,000 and
30,000. At iteration 20,000, the 21.864-second background write imposed only
0.278 seconds of completion wait. The final write was correctly joined before
evaluation and process exit.

## Retained evidence

Reference runs remain on BIGZEN under
`/home/olivier/benchmarks/dronegs-single-gpu-cell-concurrency-dev58-20260814`.
Candidate runs remain under
`/home/olivier/benchmarks/dronegs-raster-backward-active-expansion-dev59-20260814`.
The exact-commit HQ run remains under
`/home/olivier/benchmarks/aerial-gcp-hq-fastgs-backward-dev59-dde086c-20260814`.
No evidence was deleted.

| Run | Manifest SHA-256 | Metrics SHA-256 |
| --- | --- | --- |
| Reference A | `adbfc49789fb3155e01e3ba5eaacb41a0d447dee8caca9147f9cb475d5c6e4e4` | `b612297239a64ceef684398a695a4fc24e82e0b0eb579731628284f0a8401434` |
| Reference B | `89b981e5659d5d2759eb46c738ebee63ba6bcf13fa4f69a24bdf1aff280952d7` | `ea14a5f5ba30f8f84ffc8fa0f5b1bf8cd8a492328e350480debd4f16e4b8efbb` |
| Candidate A | `97a5ed9092b4a3125c3f14f839817ec7ac140961c4995ff719ea7d65f016a12b` | `e43a8e17f491a1c7807b846f471be474bf636243d0c697fe3dec64322a709819` |
| Candidate B | `da2bccd8e250cbd7f80cd7ffda9a29028d6e85007d328ad33887444cc96651c0` | `9b31295a9bce8dc3a2e4026c1c07bc0cdf8b16b542daf6b1c2264066192bee53` |

| Exact-commit HQ artifact | Size | SHA-256 |
| --- | ---: | --- |
| `point_cloud.ply` | 1,509,602,008 B | `949504d02f16fce067a16af39dfd1c67139c46f4c6eb7e12985615105a04c1d4` |
| `training.ckpt` | 4,630,800,710 B | `ef74ce1128f14f56404649086b55d8e75e2cc2138d682e1212fd0f62480c0ce1` |
| `trainer_run.json` | 7,195 B | `083ae11db3ec2cfd32e69f95534f6d223b2b26fdc73a58945d28ca5243372cca` |
| `evaluation/metrics.csv` | 1,382 B | `39d653ceb8e606c877e7d096fc448044d4c739aff8cfb83ee294cc738682a632` |

This is a trainer hot-path acceptance gate, not a full-scene product gate.
Normal/HQ multi-cell aggregation, raster seams, density, CRS and GSD remain
covered by their independent E2E qualification.
