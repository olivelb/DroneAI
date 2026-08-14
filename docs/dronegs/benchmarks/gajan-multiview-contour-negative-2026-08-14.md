# GAJAN visibility-normalized contour spike — rejected

Date: 2026-08-14

GPU: NVIDIA RTX 3090 24 GiB

Dataset: GAJAN R2S, 438 registered tiled views

## Question

The existing MRNF edge score accumulates Sobel/alpha contour support over
training views before positive-median normalization. The spike divided that
support by each Gaussian's accumulated visibility. This tested whether a
multi-view contour score should be independent of how often a Gaussian is
visible.

The candidate kept the `reference-absolute` optimizer, objective, FastGS
rasterizer, seed, capacity, image split and 7,500-step Fast schedule. It added
no render pass or persistent state. Reference and candidate used the same
instrumented binary. Retained artifacts are under:

```text
/home/olivier/benchmarks/gajan-multiview-contour-dev58-20260814
```

## Two-pair result

| Variant | Training (s) | Wall (s) | Gaussians | PSNR (dB) | Pixel PSNR (dB) | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Reference mean | 17.164 | 20.701 | 54,883 | 16.8227 | 14.4440 | 0.311106 |
| Normalized contour mean | 16.985 | 20.421 | 54,893 | 16.6249 | 14.2688 | 0.309638 |

The candidate is 1.0% faster in training, but loses **0.198 dB PSNR**,
**0.175 dB pixel-weighted PSNR** and **0.00147 SSIM**. Both pairs agree on the
direction and magnitude. Population is effectively unchanged.

## Decision

Reject the visibility-normalized formulation and remove it from the profile
registry, CLI, checkpoint enum and comparison contract. Frequently observed
edge support is useful signal on this scene; dividing by total alpha
visibility suppresses it too aggressively. A future multi-view contour method
must measure cross-view agreement explicitly rather than infer it from this
ratio. No production behavior or trainer version changed.
