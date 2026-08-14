# DroneGS dev.62 subwarp scalar Adam qualification

Date: 2026-08-15  
Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB  
Status: short repeated gate passed; exact-commit 30,000-step HQ gate pending

## Purpose

Dev.62 removes one complete model-memory pass after scalar Adam. Each
Gaussian occupies a padded 16-lane subgroup: 14 lanes update DC, opacity,
position, scale and rotation; the two padding lanes join the subgroup barrier.
The four rotation lanes then share their squared components with CUDA warp
shuffles and write the normalized quaternion in the same kernel.

This keeps the established optimizer equations and ordering. Invalid rotation
norms still fall back to the identity quaternion. The separate telemetry path
is unchanged and continues to normalize internally.

## Controlled short gate

Both candidates use the same aerial-GCP cell, seed, frozen split, 1,000
fixed-topology iterations and 5,100,000 final Gaussians. Each binary was run
twice. Dev.61 is the exact conservative-projection-culling baseline.

| Build | Run | Training (s) | Wall (s) | PSNR (dB) | SSIM | Pixel-weighted PSNR | Pixel-weighted SSIM |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev.61 | A | 37.5675 | 59.0121 | 21.86289 | 0.481362 | 21.17089 | 0.498873 |
| dev.61 | B | 37.5550 | 58.0406 | 21.86126 | 0.481527 | 21.16996 | 0.499037 |
| dev.62 | A | 35.7569 | 55.8029 | 21.86098 | 0.481537 | 21.16789 | 0.499019 |
| dev.62 | B | 35.7810 | 55.9740 | 21.86170 | 0.481590 | 21.16912 | 0.499059 |

Mean changes from dev.61 to dev.62:

- native training: 37.5613 to 35.7690 seconds, `-4.77%`;
- aggregate wall: 58.5264 to 55.8885 seconds, `-4.51%`;
- PSNR: `-0.00074 dB`;
- SSIM: `+0.000119`;
- pixel-weighted PSNR: `-0.00192 dB`;
- pixel-weighted SSIM: `+0.000084`;
- final loss: `+0.0000063`;
- final population: unchanged at 5,100,000 Gaussians.

Sampled scalar-optimizer time falls from about 7.80 ms to 5.94-5.96 ms,
approximately `-23.6%`. Sampled SH Adam remains unchanged at about
12.46-12.48 ms.

## Verification and retained evidence

- Native CPU/CUDA tests: `8/8` passed on BIGZEN.
- Candidate binary SHA-256:
  `20a69781d0ab3612bf25d006bd27bd4df6c7509cca9da76092a60907c8f5b499`.
- Dev.61 runs:
  `/home/olivier/benchmarks/dronegs-conservative-projection-culling-dev61-20260815/candidate-a`
  and `candidate-b`.
- Dev.62 runs:
  `/home/olivier/benchmarks/dronegs-subwarp-scalar-dev62-20260815/candidate-a`
  and `candidate-b`.
- Dev.62 A manifest SHA-256:
  `2f855e31270cd1d75b67599cc162086409fe493cec8d68f2854c21e2e588b6cb`;
  metrics SHA-256:
  `55df730334f9b5a6a1673dd5e8ce025d391120b79c131c6c14b6458ecbbbecdd`.
- Dev.62 B manifest SHA-256:
  `d6500ea17e00c11da9ed0091042788d470ccf8a91c3439e336e11abb1d420ce5`;
  metrics SHA-256:
  `aaf7d69da35d41c650705afd8d35ea01e1d7d12d77fc84c94a05667c4c23c96d`.

No benchmark artifact was removed. The short result authorizes the expensive
exact-commit HQ qualification; it does not by itself authorize promotion.

## Promotion gate

Build a clean checkout of the committed dev.62 source, rerun all eight native
CPU/CUDA CTests, then execute the established 30,000-step HQ cell at the
5.1 M population cap with three checkpoints. Promote only if the run exits
cleanly, its manifest and artifacts validate, and held-out quality stays
inside the existing non-regression envelope relative to dev.61.
