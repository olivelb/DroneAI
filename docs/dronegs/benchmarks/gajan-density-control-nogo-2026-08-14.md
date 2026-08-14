# GAJAN density-control prototype — no-go

Date: 2026-08-14  
Hardware: NVIDIA RTX 3090, 24 GiB  
Dataset: GAJAN R2S, 110 photographs, native crops, SH3  
Disposition: prototype removed; no production profile promoted

## Question

Could a FastGS-inspired view-consistent density controller improve detail or
reduce training cost over DroneGS MRNF while preserving the same camera split,
objective, rasterizer and seed?

The prototype sampled ten scheduled views per 500-step density window and was
tested both with bounded split selection and with FastGS-style clone-small /
split-large growth. All output and logs remain on BIGZEN under
`/home/olivier/benchmarks/gajan-vcd-20260814`; no benchmark artifact was
deleted.

## Results

| Budget/profile | Wall (s) | Final G | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|
| Fast 7,500 MRNF reference | 41.457 | 54,872 | 16.8127 | 14.4413 | 0.311098 |
| Fast 7,500 VCD, bounded split | 46.522 | 81,264 | 16.2014 | 13.8738 | 0.316075 |
| Fast 7,500 VCD, clone/split | 45.476 | 82,532 | 16.1477 | 13.8586 | 0.298707 |
| Normal 15,000 MRNF reference | 164.817 | 191,600 | 18.9780 | 16.4689 | 0.373993 |
| Normal 15,000 VCD | 209.932 | 293,383 | 17.6973 | 14.9425 | 0.380437 |

At Normal budget the candidate is 27% slower, creates 53% more Gaussians and
loses 1.53 dB pixel-weighted PSNR for a 0.00644 SSIM increase. The clone/split
variant also loses SSIM in Fast. This is not a balanced quality/speed gain.

## Decision

Remove the density profile, CLI/schema surface and its native tests. Keep MRNF
as the only density controller and use sampled GPU stage telemetry to target
measured bottlenecks. Any future sparse-pixel or sparse-primitive work must be
a new isolated A/B and must preserve PSNR, SSIM, deterministic topology and
the requested post-filter GSD gate.
