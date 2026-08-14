# GAJAN redundant projected-record sort removal — dev.53

Date: 2026-08-14

Hardware: NVIDIA RTX 3090, 24 GiB

Dataset: GAJAN R2S, 110 photographs, native 2×2 crops, SH3

Decision: accepted

## Change under test

The persistent trainer previously sorted all projected records globally by
depth before duplicating them into tiles. The following radix sort already
orders every duplicate by `(tile, depth)`, while the original Gaussian index
is retained in `depth_keys`. The first sort and its 52-byte record output plus
64-bit key output buffers were therefore redundant.

Dev.53 feeds projected records directly into pair counting, duplication,
rendering and backward propagation. The tile/depth sort, stable equal-depth
ordering, source indices, renderer, objective, backward pass, optimizer and
topology schedule remain unchanged. Sampled telemetry now decomposes
preprocessing into projection, record sort, binning, pair sort and FastGS
bucket construction while retaining the aggregate `preprocess_ms` field.

## Results

| Run | Wall (s) | Train (s) | Final G | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| dev.52 run 1 | 29.333 | 26.077 | 54,888 | 16.8122 | 14.4329 | 0.311048 |
| dev.52 run 2 | 29.335 | 26.078 | 54,878 | 16.8194 | 14.4280 | 0.311094 |
| dev.53 run 1 | 27.087 | 23.813 | 54,899 | 16.8250 | 14.4436 | 0.311113 |
| dev.53 run 2 | 27.074 | 23.815 | 54,875 | 16.8169 | 14.4333 | 0.311030 |

The two-run mean improves from 29.334 to 27.080 seconds wall (`-7.7%`) and
from 26.077 to 23.814 seconds training (`-8.7%`). Mean PSNR improves by
0.0052 dB, pixel-weighted PSNR by 0.0080 dB and SSIM is unchanged to six
decimal places. Mean final population is 54,887 versus 54,883 for dev.52.

## Stage evidence

The instrumented control measured 0.452 ms mean for the redundant record sort
and 1.507 ms for all preprocessing. With the sort removed, the corresponding
event interval is 0.012 ms and preprocessing averages 1.171 ms across the two
candidate runs. The remaining measured preprocessing costs are projection and
the required tile/depth pair sort; optimizing either now requires a separate
quality-preserving A/B.

All eight native CPU/CUDA CTests pass on the RTX 3090. Logs and artifacts are
retained on BIGZEN under
`/home/olivier/benchmarks/gajan-preprocess-20260814`.
