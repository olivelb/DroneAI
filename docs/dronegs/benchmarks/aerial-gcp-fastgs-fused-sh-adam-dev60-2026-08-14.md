# Aerial GCP fused FastGS SH-Adam qualification — 2026-08-14

## Verdict

The dev.60 fused FastGS appearance/Adam path passes the repeated
fixed-topology 5.1 M-Gaussian gate. Mean native training falls from 42.311
seconds in dev.59 to 39.034 seconds (`-7.74%`) and from 46.848 seconds in the
dev.58 baseline (`-16.68%`). Aggregate two-run wall time improves `4.07%`
from dev.59 and `10.93%` from dev.58. Held-out loss, PSNR, SSIM and Gaussian
population remain equivalent.

Dev.60 retains dev.59's four raw appearance atomics per source/tile, but no
longer materializes their expanded color-SH and opacity-SH gradients. Scalar
Adam applies the DC constant directly; coefficient-parallel SH Adam applies
the projected basis directly. The SH optimizer still visits every resident
Gaussian, including temporarily invisible ones, so zero-gradient Adam moment
decay and optimizer semantics are preserved. Structural FastGS also skips the
now-unused expanded-gradient buffer clears.

## Controlled setup

| Item | dev.58 | dev.59 | dev.60 candidate |
| --- | --- | --- | --- |
| Host / GPU | BIGZEN / RTX 3090 24 GiB | same | same |
| Build | Release, CUDA 12.0, `sm_86` | same | same |
| Candidate binary SHA-256 | — | — | `285f5724d41445e64031d3f3a3c59c7172f2a0e94476eae2a096b4f892c86a35` |
| Dataset fingerprint | `fnv1a64:v3:b75a413130a5daa2` | same | same |
| Training / held-out views | 41 / 5 | same | same |
| Iterations / population | 1,000 / 5,100,000 fixed | same | same |
| Optimizer / raster | AbsGrad 0.50 / FastGS | same | same |
| SH / seed | SH3 / 42 | same | same |
| Checkpoint / topology | disabled / fixed | same | same |

The candidate Release build passed all eight native CPU/CUDA CTests before
the benchmark. Both candidate processes exited normally without OOM or
non-finite error. The pre-commit binary version remains dev.59; its source
contents are the fused implementation recorded as dev.60 with this report.

## Repeated runtime and quality

| Version | Mean wall | Mean training | Mean PSNR | Mean SSIM | Mean loss |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev.58 | 66.998 s | 46.848 s | 21.86243 dB | 0.481399 | 0.0230897 |
| dev.59 | 62.202 s | 42.311 s | 21.85970 dB | 0.481407 | 0.0230855 |
| dev.60 | 59.658 s | 39.034 s | 21.85816 dB | 0.481299 | 0.0230873 |

| Delta | Training | Two-run wall | PSNR | SSIM |
| --- | ---: | ---: | ---: | ---: |
| dev.60 vs dev.59 | **-7.74%** | **-4.07%** | -0.00154 dB | -0.000108 |
| dev.60 vs dev.58 | **-16.68%** | **-10.93%** | -0.00426 dB | -0.000101 |

At sampled iteration 999, dev.60 reduces gradient reset from about 1.85 to
0.47 ms, raster backward from 9.49 to about 8.10 ms and SH Adam from 13.26 to
12.48 ms. Total sampled GPU-step time is 39.7–39.9 ms versus 43.1 ms in
dev.59 and 48.3 ms in dev.58. Preprocessing remains unchanged within run
noise.

## Retained evidence

The dev.60 candidate remains on BIGZEN under
`/home/olivier/benchmarks/dronegs-fused-fastgs-sh-adam-dev60-20260814`.
Its paired dev.59 reference remains under
`/home/olivier/benchmarks/dronegs-raster-backward-active-expansion-dev59-20260814`,
and the dev.58 baseline remains under
`/home/olivier/benchmarks/dronegs-single-gpu-cell-concurrency-dev58-20260814`.
No evidence was deleted.

| Candidate run | Manifest SHA-256 | Metrics SHA-256 |
| --- | --- | --- |
| A | `294700ca5f4e77a58ccbceb9b81b6fabb55774d7928b7ebfef8089ce74510032` | `fded2158f477030072070d20f48dc88d6105bd0b2d9a7ad98fa9f38ca81bdbc0` |
| B | `ceb2c32cb9f54189e33011d295484077e0318121c65feaf585fde9377313fda1` | `b7cbe7aac1135fe734ca2f5538eb452a9baeda4ac8e3c9f2b62547b338d73a59` |

The exact-commit 30,000-step HQ gate is the remaining promotion condition for
dev.60. Full-scene multi-cell aggregation, raster seams, density, CRS and GSD
remain independent product gates.
