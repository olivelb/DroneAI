# Phase 4 Albagnac persistent ordered-alpha trainer result

Date: 2026-07-25
Status: persistent-integration and real-scene convergence sub-gates passed;
not a LichtFeld quality-parity result

## Change

`0.5.0-dev.8` connects the validated ordered-alpha forward/backward to a
persistent training context:

- Gaussians, projected records, depth keys, pair scans, CUB temporary storage,
  tile pairs/ranges, image gradients, and Adam moments stay allocated on CUDA.
- Pair and tile buffers grow only when a frame exceeds their previous
  high-water marks.
- The host uploads one RGB8 target per frame.
- Active-pixel L1 gradients, ordered-alpha backward, and DC/opacity Adam remain
  on device.
- The host reads pair-count and loss/active-pixel scalars, but never reads
  per-Gaussian gradients during training.

The experimental DroneGS binary now selects this path. The additive
implementation remains available as a synthetic convergence control.

## Correctness gates

- Existing CPU/CUDA forward and backward parity suites pass.
- CPU and direct CUDA finite-difference suites pass.
- The training test runs the additive and ordered-alpha paths from the same
  initialized 25-Gaussian scene; both reduce their own anchor loss by more than
  5% over 30 iterations.
- The ordered-alpha synthetic loss decreases monotonically from `0.346274` at
  iteration 1 to `0.313733` at iteration 30.

## Real workload

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Registered / training images | 1,376 / 1,376 |
| Training resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Host image cache | 256 MiB RGB8 LRU |
| Build | Release, `sm_89`, CUDA 12.8.93, `--use_fast_math` |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev8-ordered-500-sm89/` |

The run used the dev.8 working tree based on commit `e995433`. The CMake build
provenance dependency is corrected in dev.8 so clean post-commit builds refresh
the exact revision automatically.

## Result

| Metric | Persistent ordered-alpha |
|---|---:|
| Initial anchor L1 | 0.20055936 |
| Final anchor L1 | 0.15530634 |
| Anchor reduction | 22.56% |
| End-to-end wall | 25.804 s |
| Throughput | 19.38 iterations/s |
| Trainer compute | 14.350 s |
| Foreground image wait | 10.315 s |
| JPEG decoder service | 23.835 s |
| Startup | 0.348 s |
| PLY export | 0.208 s |
| Final Gaussians | 1,025,093 |
| PLY bytes | 57,405,638 |

PLY SHA-256:
`fc0c4d5210922c73627c6a9932baa89d71a5995da830f53733258892dd64ead1`.

The previous additive dev.3 warm median was 59.629 seconds, or 8.39
iterations/s. The ordered run is 56.7% shorter and has 131.1% higher
end-to-end throughput. This is an engineering throughput comparison only:
ordered-alpha and normalized additive anchor losses are different objectives,
and neither run contains held-out PSNR/SSIM/LPIPS.

## Memory

A separate 100-iteration run sampled total GPU memory every 100 ms:

| Sample | MiB |
|---|---:|
| Baseline total GPU memory | 822 |
| Peak total GPU memory | 1,473 |
| Peak total-memory delta | 651 |

That run completed in 6.649 seconds and reduced anchor L1 from `0.200559` to
`0.174913`. The 500-iteration process reported a maximum host RSS of
518,108 KiB. No CUDA OOM occurred.

## New bottleneck

The previous one-slot JPEG prefetch was effective when additive GPU compute was
slower than decode. Ordered-alpha persistent compute now outruns the decoder:

- only 71 of 499 prefetched images were ready at demand time (14.2%);
- foreground image wait is 10.315 seconds;
- trainer compute is 14.350 seconds.

A deeper bounded decode queue or small decoded-image worker pool is now a
measured optimization candidate. It must preserve the 256 MiB resident LRU
bound and deterministic camera schedule.

## Decision

Accept persistent ordered-alpha as the default path of the experimental
DroneGS binary:

- synthetic and real convergence pass;
- the complete million-Gaussian scene remains well within the 8 GiB GPU;
- the real 500-iteration wall time improves materially;
- Gaussian gradients and Adam no longer round-trip through the host.

No `dronegs-v0.5.0` tag is created. Anisotropic covariance,
position/scale/rotation gradients, DSSIM, progressive SH, and held-out
LichtFeld quality parity remain open.
