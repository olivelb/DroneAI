# GAJAN coefficient-parallel SH Adam — dev.52

Date: 2026-08-14  
Hardware: NVIDIA RTX 3090, 24 GiB  
Dataset: GAJAN R2S, 110 photographs, native 2×2 crops, SH3  
Decision: accepted

## Change under test

Dev.51 updated up to 45 active color-SH and 15 opacity-SH coefficients in
serial loops inside one CUDA thread per Gaussian. Dev.52 assigns one thread to
one active coefficient. DC, opacity, position, scale, rotation, Adam moments,
bias correction, learning rates, progressive-SH activation and the training
schedule are unchanged. The new kernel uses the existing arrays and adds no
persistent GPU allocation.

## Results

| Budget/run | Wall (s) | Train (s) | Final G | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Fast dev.51 control | 38.985 | 35.684 | 54,881 | 16.8101 | 14.4438 | 0.311236 |
| Fast dev.52 run 1 | 29.333 | 26.077 | 54,888 | 16.8122 | 14.4329 | 0.311048 |
| Fast dev.52 run 2 | 29.335 | 26.078 | 54,878 | 16.8194 | 14.4280 | 0.311094 |
| Normal dev.51 control | 162.594 | 158.849 | 191,600 | 18.9780 | 16.4689 | 0.373993 |
| Normal dev.52 | 84.472 | 80.983 | 191,283 | 18.9865 | 16.4947 | 0.374458 |

The two Fast candidate walls differ by 0.002 seconds. Their small topology and
quality dispersion is within the retained reference-run variation. Normal
improves every reported held-out quality metric while halving training time.
Observed Normal VRAM stayed below 5.2 GiB, inside the 8 GiB qualification
envelope.

## Stage evidence

At Fast step 4,501, the staggered dev.51 sample measured 2.318 ms in Adam;
dev.52 measured 0.328 ms (`-85.9%`). Total sampled GPU work fell from 6.715 to
4.298 ms (`-36.0%`). At Normal step 12,001, the remaining leading costs are
preprocessing, objective and backward; SH Adam is no longer the dominant
bottleneck.

All artifacts and logs are retained on BIGZEN under
`/home/olivier/benchmarks/gajan-profile-20260814`. Promotion still requires
the normal repository CI; these real-GPU results do not cause long CUDA or
COLMAP builds to run on unrelated pull requests.
