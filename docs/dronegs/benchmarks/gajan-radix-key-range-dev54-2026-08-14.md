# GAJAN bounded tile/depth radix range — dev.54

Date: 2026-08-14

Hardware: NVIDIA RTX 3090, 24 GiB

Dataset: GAJAN R2S, 110 photographs, native 2×2 crops, SH3

Decision: accepted

## Change under test

Tile/depth pair keys use the lower 32 bits for IEEE-754 depth and the upper
32 bits for a zero-based tile identifier. The persistent CUB radix sort
previously processed all 64 bits even when the image contained far fewer than
2^32 tiles.

Dev.54 computes the exclusive end bit as
`32 + bit_width(tile_count - 1)`. Every depth bit and every active tile bit is
still sorted; only constant zero bits above the maximum tile identifier are
excluded. Key construction, stable sorting, pair values and all scientific
training operations are unchanged.

## Results

| Run | Wall (s) | Train (s) | Final G | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| dev.53 run 1 | 27.087 | 23.813 | 54,899 | 16.8250 | 14.4436 | 0.311113 |
| dev.53 run 2 | 27.074 | 23.815 | 54,875 | 16.8169 | 14.4333 | 0.311030 |
| dev.54 run 1 | 26.617 | 23.063 | 54,889 | 16.8252 | 14.4410 | 0.311228 |
| dev.54 run 2 | 26.492 | 23.217 | 54,901 | 16.8070 | 14.4236 | 0.311224 |

The two-run mean improves from 27.080 to 26.554 seconds wall (`-1.9%`) and
from 23.814 to 23.140 seconds training (`-2.8%`). Mean final population is
54,895 versus 54,887. Mean PSNR changes by `-0.0049` dB, pixel-weighted PSNR
by `-0.0061` dB and SSIM by `+0.000154`; these differences remain inside the
retained Fast-run variation.

The sampled pair-sort stage improves from 0.357 to 0.280 ms mean (`-21.5%`).
The end-to-end gain is necessarily smaller because projection, objective,
backward, decoding, evaluation and export are unaffected.

All eight native CPU/CUDA CTests pass on the RTX 3090. Logs and artifacts are
retained on BIGZEN under
`/home/olivier/benchmarks/gajan-preprocess-20260814`.

## Normal 15,000-step qualification

The cumulative dev.52–dev.54 optimization was also repeated twice with the
Normal budget, 3 million requested capacity and otherwise identical inputs.

| Run | Wall (s) | Train (s) | Final G | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| dev.52 reference | 84.472 | 80.983 | 191,283 | 18.9865 | 16.4947 | 0.374458 |
| dev.54 run 1 | 77.066 | 73.554 | 191,454 | 18.9587 | 16.4419 | 0.373886 |
| dev.54 run 2 | 76.602 | 73.059 | 191,280 | 18.9778 | 16.4827 | 0.374190 |

The dev.54 mean is 76.834 seconds wall (`-9.0%`) and 73.306 seconds training
(`-9.5%`) versus dev.52. Mean PSNR is 18.9683 dB, pixel-weighted PSNR is
16.4623 dB and SSIM is 0.374038. These values remain within the retained
dev.50–dev.52 run envelope; the second dev.54 repetition nearly reproduces
the former quality reference. Observed VRAM was approximately 5.0 GiB, well
inside the 8 GiB Normal deployment envelope.
