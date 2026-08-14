# GAJAN AbsGrad densification screen — dev.57

Date: 2026-08-14

GPU: NVIDIA RTX 3090 24 GiB

Dataset: GAJAN R2S, 438 registered tiled views

Binary SHA-256: `7a73422a864f550460a90d7fc87b5680493e0d69176c8dfa92a76afeba510482`

## Purpose

This screen compares the validated `reference-absolute` optimizer with the
neutral `reference-absolute-absgrad025` and
`reference-absolute-absgrad050` profiles already present in DroneGS. The
experimental profiles keep every optimizer rate, objective, topology cadence,
capacity, seed and raster setting unchanged. They add only a 0.25 or 0.50
homodirectional absolute projected-gradient contribution to MRNF growth
ranking, following the failure mode described by AbsGS.

All runs use seed 42, structural FastGS, tile mode 4, projected-KNN
initialization, a 1,000-step topology cooldown and the validated photometric
finish. Artifacts and manifests remain under:

```text
/home/olivier/benchmarks/gajan-absgrad-dev57-20260814
```

## Fast 7,500-step screen

Values are two-run means.

| Profile | Training (s) | Wall (s) | Gaussians | PSNR (dB) | Pixel PSNR (dB) | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Reference | 17.093 | 20.511 | 54,877 | 16.8277 | 14.4560 | 0.311225 |
| AbsGrad 0.25 | 17.096 | 20.504 | 54,889 | 16.8935 | 14.5132 | 0.312392 |
| AbsGrad 0.50 | 17.116 | 20.677 | 54,882 | 16.9525 | 14.5823 | 0.313456 |

AbsGrad 0.50 gains **0.125 dB PSNR**, **0.126 dB pixel-weighted PSNR** and
**0.00223 SSIM** for 0.14% more training time and effectively the same
population. Both repetitions agree on the quality direction.

## Normal 15,000-step confirmation

Only the reference and selected 0.50 candidate were repeated at Normal.

| Profile | Training (s) | Wall (s) | Gaussians | PSNR (dB) | Pixel PSNR (dB) | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Reference | 55.228 | 58.905 | 191,399 | 18.9752 | 16.4895 | 0.374224 |
| AbsGrad 0.50 | 55.011 | 58.632 | 191,245 | 19.0931 | 16.6234 | 0.377395 |

AbsGrad 0.50 gains **0.118 dB PSNR**, **0.134 dB pixel-weighted PSNR** and
**0.00317 SSIM** while training is 0.4% faster and final population is 0.08%
smaller. It therefore passes this image-space screening gate without a speed
or capacity trade-off.

## Decision

Select `reference-absolute-absgrad050` for the representative native-block
A/B. Keep it experimental and keep `reference-absolute` as production. The
next gate must compare final ortho/DEM residuals, edge MTF, floaters, seams,
LPIPS, peak VRAM and wall time; GAJAN held-out images alone cannot authorize a
map/facade default change. AbsGrad 0.25 remains reproducible but is not the
priority candidate.
