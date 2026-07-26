# Phase 4 Ada radix and occupancy tuning

Date: 2026-07-26

Version: `0.5.0-dev.28`

## Decision

Accept CUB Policy610 for both stable radix sorts and a 64-register ceiling for
native architecture-89 CUDA compilation. Keep DroneGS opt-in and keep the
overall Phase 4 quality/speed parity gate open.

The retained configuration is faster than dev.27 on both million-splat scenes
and beats the dev.26 PTX reference in Savères wall time. Albagnac remains 4.3%
slower in wall time than dev.26, so broad speed parity is not yet demonstrated.
Held-out PSNR, SSIM, exact-pair LPIPS, and large-scene topology remain stable.

## Root cause and retained implementation

The dev.27 compact algorithm compiled for compute 52 and JIT-compiled by the
driver trained Savères in 53.96 seconds, versus 58.25 seconds for the native
sm_89 binary. This isolated the regression to CUDA/CUB policy and code
generation rather than the projected-record algorithm.

Dev.28:

- retains the deterministic 64-bit `(positive depth bits, source index)` key;
- retains the 48-byte projected render record and separate source-indexed SH
  bases from dev.27;
- dispatches both projected-depth and tile/depth pair sorts through stable CUB
  Policy610 instead of CUDA 12.8's native Ada default policy;
- adds `--maxrregcount=64` only when CMake compiles architecture 89.

The GPL boundary is unchanged: the radix adapter is in
`app1-colmap/dronegs/cuda/rasterization.cu`, already distributed as
GPL-3.0-or-later because it contains the MRNF-derived implementation. The
architecture-specific compiler option and documentation remain under their
existing licenses.

## Validation inputs

Only existing COLMAP dense outputs were mounted read-only. No photos, feature
database, match graph, sparse model, bundle adjustment, or undistortion result
was modified. No COLMAP rerun and no combined approximately 2,000-photo
Albagnac test was performed.

| Scene | Images | Initial Gaussians | Iterations | Held-out |
|---|---:|---:|---:|---:|
| GAJAN smoke | 25 | 9,324 | 1,200 | 5 |
| Savères Mavic 3E RTK | 1,065 | 642,161 | 220 | 17 |
| Albagnac Mavic 3E RTK | 1,376 | 1,025,093 | 220 | 22 |

## Throughput

| Scene | Train dev.26 | Train dev.27 | Train dev.28 | dev.28 vs dev.27 | Wall dev.26 | Wall dev.27 | Wall dev.28 | dev.28 vs dev.26 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GAJAN | 14.125 s | 13.309 s | 13.443 s | +1.0% | 16.786 s | 14.178 s | 14.430 s | -14.0% |
| Savères | 53.125 s | 58.247 s | 55.772 s | -4.3% | 62.492 s | 62.765 s | 61.561 s | -1.5% |
| Albagnac | 75.456 s | 82.987 s | 79.872 s | -3.8% | 83.941 s | 88.957 s | 87.522 s | +4.3% |

The GAJAN result is dominated by fixed startup/evaluation costs and remains
substantially faster than dev.26. Savères reaches wall-time parity or better.
Albagnac improves materially over dev.27 but does not yet pass the PTX
wall-time reference.

## Quality, perceptual metric, and topology

| Scene | PSNR dev.28 | SSIM dev.28 | LPIPS dev.28 | Final Gaussians |
|---|---:|---:|---:|---:|
| GAJAN | 14.144094 | 0.207099 | 1.047440 | 13,991 |
| Savères | 15.720999 | 0.110610 | 1.201677 | 687,110 |
| Albagnac | 16.869730 | 0.240971 | 1.085558 | 1,096,829 |

Against dev.26, Savères changes by -0.000163 dB PSNR, -0.000003 SSIM,
and +0.000007 LPIPS. Albagnac changes by -0.000046 dB PSNR, effectively zero
SSIM, and -0.000056 LPIPS. These bounded-run differences are numerical noise.
Savères and Albagnac final topology exactly match dev.26 and dev.27.

LPIPS uses official package version 0.1, AlexNet, exact final RGB8 PPM pairs,
and `[-1, 1]` normalization. Dev.28 means are over 5, 17, and 22 held-out
views respectively.

## Optimization sweep

The synthetic 642,161-splat forward-plus-backward benchmark selected the radix
policy before expensive scene runs:

| Radix policy | Median |
|---|---:|
| Policy500 | 122.812 ms |
| Policy600 | 122.910 ms |
| Policy610 | 115.679 ms |
| Policy620 | 120.118 ms |
| Policy700 | 120.722 ms |

Further controlled results:

- Policy610 only for projected records and the Ada default for tile pairs:
  121.183 ms; rejected.
- Native sm_86 cubin with Policy610 on Ada: 146.472 ms; rejected.
- 96-register ceiling: 118.507 ms versus 118.986 ms unrestricted in the
  longer Policy610 comparison; neutral and rejected.
- 64-register ceiling: 114.627 ms; retained and confirmed on both real scenes.
- 48-register ceiling: 122.674 ms; rejected because spill pressure dominates.
- 32-bit depth key plus a 52-byte value, with Policy610: 59.78 seconds
  Savères training; rejected.
- 32-bit depth key plus limited tile bits under the default Ada policy:
  59.31 seconds Savères training; rejected.

## Automated evidence

- Clean release build identifies itself as `0.5.0-dev.28`.
- `cuobjdump --list-elf` reports only `dronegs.1.sm_89.cubin`.
- Six of six core, CPU rasterization, CUDA loss, CUDA rasterization, CUDA
  training, and dependency-free LPIPS-tool suites pass.
- Equal-depth stable ordering and CPU/CUDA forward/backward parity remain
  covered by the existing tests.
- The isolated evaluator image `dronegs-lpips:0.5.0-dev.28` is built and used
  for all three retained outputs.

## Next optimization gates

1. Add CUDA-event stage telemetry around projection, projected sort, tile
   duplication/sort, rendering, backward, and optimizer updates because GPU
   performance counters are disabled on this machine.
2. Replace the projected-record general radix sort with a purpose-built
   depth-order primitive only if stage timing shows enough headroom.
3. Fuse or device-reside host-mediated topology compaction before long
   multi-refinement throughput claims.
4. Add checkpoint/resume before convergence-length runs.
5. Run same-view convergence controls against pinned LichtFeld and complete
   visual plus downstream orthomosaic/detection non-regression.
