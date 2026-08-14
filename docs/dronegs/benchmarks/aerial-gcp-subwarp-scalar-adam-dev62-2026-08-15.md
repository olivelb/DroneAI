# DroneGS dev.62 subwarp scalar Adam qualification

Date: 2026-08-15  
Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB  
Status: short repeated gate and exact-commit 30,000-step HQ gate passed

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
- Short-gate candidate binary SHA-256:
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

No benchmark artifact was removed.

## Exact-commit 30,000-step HQ gate

Commit `d8c4df5` was transferred as a Git bundle into a clean detached BIGZEN
checkout and built in Release mode. All eight native CPU/CUDA CTests passed.
The exact binary reports `0.5.0-dev.62` and has SHA-256
`bfda99726fe182751d0190c718c8a849e9ae55fcf6a6ab25a3f8cf16dfc5ec5d`.

The established 30,000-step HQ cell completed projected-KNN initialization,
capacity-targeted refinement, SH3 training, the fixed-topology cooldown, the
1,000-step MSE finish and exactly three checkpoints. It reached 5.1 M
Gaussians without OOM, non-finite value or fatal log entry. The detached
launcher did not persist a separate numeric exit-status file; the process
terminated through the normal success path with a `completed` manifest, no
manifest error and all final artifacts present.

| Metric | dev.61 HQ | dev.62 HQ | Delta |
| --- | ---: | ---: | ---: |
| Native training | 918.516 s | 884.450 s | **-3.71%** |
| Wall time | 930.127 s | 896.056 s | **-3.66%** |
| Final loss | 0.0229636 | 0.0228701 | -0.0000934 |
| Mean PSNR | 21.8484 dB | 21.8412 dB | -0.0072 dB |
| Pixel-weighted PSNR | 21.1649 dB | 21.1349 dB | -0.0300 dB |
| Mean SSIM | 0.479068 | 0.480750 | +0.001682 |
| Pixel-weighted SSIM | 0.497613 | 0.497699 | +0.000086 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Checkpoints | 3 | 3 | 0 |

The PSNR changes are small and mean SSIM improves while remaining within the
established 0.002 long-run envelope. At iteration 29,999, scalar Adam takes
5.972 ms versus about 7.8 ms on dev.61; SH Adam remains 12.491 ms and the
complete GPU step takes 37.181 ms. Rotation telemetry remains exactly 0.5 RMS
at iteration 30,000.

Visual inspection of held-out view 2 against its target and the dev.61
prediction found equivalent texture, support and colour, with no new hole,
tear, boundary or blur artifact.

## Exact retained evidence

- Dev.62 run:
  `/home/olivier/benchmarks/aerial-gcp-hq-subwarp-scalar-adam-dev62-d8c4df5-20260815/cell0`.
- Dev.61 reference:
  `/home/olivier/benchmarks/aerial-gcp-hq-conservative-projection-culling-dev61-42ff1a5-20260815/cell0-r2`.
- Manifest SHA-256:
  `af63db47516673290944874dbf2db4bbc46910341e2ec176d61842d941de3184`.
- Evaluation CSV SHA-256:
  `d22f7d55c1cdd7633addc5b6e6cd759986fb272a32006ee84f78e366fdfa9876`.
- Final PLY SHA-256:
  `ac90a48be51b3a48b22b652d2d53a3c904f3adb2726d3e4116379485af93447f`.

All five prediction/target pairs are retained under `evaluation/`; their
individual hashes were captured during qualification. No benchmark artifact
was removed.
