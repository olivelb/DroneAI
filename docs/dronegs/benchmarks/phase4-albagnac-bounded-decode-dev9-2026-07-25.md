# Phase 4 Albagnac bounded JPEG decode experiments

Date: 2026-07-25
Status: infrastructure accepted; tested performance candidates rejected as
defaults

## Change

`0.5.0-dev.9` generalizes the persistent single prefetch slot into an ordered
bounded queue:

- queue depth and worker count are independently configurable;
- duplicate scheduled indices are decoded once;
- workers may complete out of order, but training consumes the deterministic
  camera schedule in order;
- only the training thread mutates the 256 MiB LRU;
- queue capacity, refill, concurrent decode, and duplicate handling have native
  tests.

An opt-in libjpeg reduced-IDCT mode selects the closest native 1/2, 1/4, or 1/8
decode scale that is no smaller than the requested training dimensions. It
avoids decoding all source pixels before a final resize. This mode changes the
filtered RGB target and is evaluated separately.

The default remains one prefetch slot, one worker, and full JPEG decode.

## Workload

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| CPU | Intel Core i9-13900H, 20 logical CPUs exposed to WSL |
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Training resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Seed | 42 |
| Host LRU | 256 MiB RGB8 |
| Base commit | `3dd3a6a` plus dev.9 working tree |

At this resolution, each queued RGB8 target is 1,392,000 bytes. A depth-eight
queue therefore bounds completed out-of-LRU targets to about 11.1 MB for this
dataset.

## Queue sweep, 100 iterations

All runs use full JPEG decode.

| Depth / workers | Wall (s) | Foreground wait (s) | Trainer compute (s) | Decoder service (s) | Ready / started |
|---|---:|---:|---:|---:|---:|
| 1 / 1 | 6.76 | 1.69 | 3.64 | 5.01 | 28 / 100 |
| 4 / 2 | 6.70 | 0.05 | 5.01 | 5.29 | 100 / 100 |
| 8 / 2 | 6.61 | 0.06 | 4.99 | 5.30 | 100 / 100 |
| 8 / 4 | 6.90 | 0.06 | 5.14 | 5.52 | 100 / 100 |
| 16 / 4 | 7.43 | 0.08 | 5.10 | 7.61 | 100 / 100 |

Two workers hide JPEG latency, but concurrent CPU decode increases observed GPU
training wall time. On this laptop, CPU and GPU share thermal and power limits;
removing foreground wait does not directly translate into end-to-end gain.

## Queue confirmation, 500 iterations

| Depth / workers | Wall (s) | Foreground wait (s) | Trainer compute (s) | Decoder service (s) | Ready / started |
|---|---:|---:|---:|---:|---:|
| 1 / 1 control | 27.705 | 8.970 | 17.073 | 24.823 | 107 / 499 |
| 8 / 2 | 28.695 | 0.136 | 26.954 | 27.774 | 499 / 499 |

Depth eight with two workers is 3.6% slower end to end even though it makes
every prefetched image ready. It is rejected as the default.

A later depth-eight / one-worker run completed in 29.465 seconds, with 7.111
seconds of wait and 257 ready images. It ran later in the thermally loaded
sequence and is not used for the primary percentage comparison; it provides no
evidence for changing the default.

The clean dev.8 Release/sm_89 reference remains 25.804 seconds. All dev.9
experiments in this report ran after repeated CUDA workloads, so decisions use
same-cycle controls rather than claiming a cross-cycle absolute speedup.

## Reduced-IDCT experiment

The 500-iteration reduced-IDCT run used depth one and one worker:

| Metric | Full-decode control | Reduced IDCT |
|---|---:|---:|
| Wall (s) | 27.705 | 27.118 |
| Foreground wait (s) | 8.970 | 3.974 |
| Trainer compute (s) | 17.073 | 21.582 |
| Decoder service (s) | 24.823 | 21.802 |
| Ready / started | 107 / 499 | 252 / 499 |
| Initial anchor L1 | 0.200559 | 0.191886 |
| Final anchor L1 | 0.155307 | 0.144332 |

Wall time is 2.1% shorter than the same-cycle full-decode control. This is not
a quality-neutral comparison: reduced IDCT filters target pixels differently,
as confirmed by the changed initial anchor loss. It remains opt-in until
held-out PSNR, SSIM, LPIPS, and orthomosaic checks exist.

## Correctness and provenance

- All five native suites pass, including CUDA convergence.
- The cache test forces two simultaneous loaders, verifies queue capacity,
  consumes out-of-order worker completions in schedule order, and refills the
  queue.
- Default CLI values reproduce dev.8 behavior.
- Queue and orchestration code are original MIT implementation.
- The existing system libjpeg dependency is used through its public scaled-IDCT
  API. No LichtFeld or other GPL implementation code is copied.

## Decision

Accept the bounded queue and explicit A/B controls as experimental
infrastructure, but keep the measured defaults:

- `--prefetch-depth 1`;
- `--decode-workers 1`;
- `--jpeg-idct-scale 0`.

The next Phase 4 priority returns to correctness: anisotropic covariance
projection and position/scale/rotation gradients. JPEG optimization should be
revisited only with a quality-neutral lower-work decoder or after held-out
quality gates can evaluate reduced IDCT.
