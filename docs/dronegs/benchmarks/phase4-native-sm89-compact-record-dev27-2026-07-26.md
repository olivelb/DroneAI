# Phase 4 native sm_89 compact-record validation

Date: 2026-07-26

Version under test: `0.5.0-dev.27`

Reference: dev.25 trainer validated and documented by dev.26. Its binary
contained an sm_52 cubin plus compute_52 PTX and was JIT-compiled by the
driver on the RTX 4070 Laptop GPU.

## Decision

Accept the compact 48-byte projected render record as the native-sm_89
implementation. Keep DroneGS opt-in and keep the Phase 4 production gate open.

The implementation fixes the CUDA 12.8 device-link failure, reduces persistent
projected-depth capacity, passes every existing CPU/CUDA/LPIPS-tool suite, and
preserves bounded three-scene PSNR, SSIM, LPIPS, topology, and PLY output.
It improves GAJAN throughput and is wall-neutral on Savères, but it remains
slower than the PTX-JIT reference on million-splat Albagnac. Native
architecture targeting is therefore complete; broad speed parity is not.

## Implementation

The dev.26 projected-depth radix sort carried a 144-byte
`DeviceProjectedRecord` as the CUB value. CUDA 12.8 Policy900 emitted
50.5--51.5 KiB of static shared memory, exceeding the 48 KiB device-link
limit for sm_86 and sm_89.

Dev.27:

- establishes `89-real;89-virtual` before CMake enables CUDA;
- keeps the deterministic 64-bit `(depth bits, source index)` key;
- sorts a 48-byte record containing only projected geometry, opacity, and RGB;
- stores the 16 SH bases in a separate source-indexed buffer;
- reconstructs tile bounds from projected center/radius in lightweight
  coalesced kernels;
- recovers the source index from the sorted key during backward;
- avoids a full-record gather.

The persistent projected-depth workspace changes from two 144-byte records
per reserved Gaussian to two 48-byte records, 64 bytes of SH bases, and
16 bytes of depth keys: 288 to 176 bytes, a reduction of 112 bytes per
capacity slot. At Albagnac's cap of 1,120,000 this is 125,440,000 bytes
(119.6 MiB), excluding common tile-pair and optimizer buffers.

## Validation inputs

Existing undistorted COLMAP dense workspaces were mounted read-only. No source
photo, sparse model, feature database, match graph, bundle adjustment, or
undistortion output was modified.

| Scene | Images | Initial Gaussians | Iterations | Held-out |
|---|---:|---:|---:|---:|
| GAJAN smoke | 25 | 9,324 | 1,200 | 5 |
| Savères Mavic 3E RTK | 1,065 | 642,161 | 220 | 17 |
| Albagnac Mavic 3E RTK | 1,376 | 1,025,093 | 220 | 22 |

The optimizer profile, progressive SH schedule, split, seed, cache, resize,
held-out rule, MRNF cadence, and output settings are identical to dev.26.
No combined approximately 2,000-photo Albagnac test and no COLMAP rerun is
part of this phase.

## Quality and perceptual results

| Scene | PSNR dev.26 | PSNR dev.27 | Delta | SSIM dev.26 | SSIM dev.27 | Delta |
|---|---:|---:|---:|---:|---:|---:|
| GAJAN | 14.132559 | 14.162875 | +0.030316 | 0.205984 | 0.206365 | +0.000381 |
| Savères | 15.721162 | 15.721121 | -0.000041 | 0.110614 | 0.110610 | -0.000004 |
| Albagnac | 16.869776 | 16.869761 | -0.000015 | 0.240971 | 0.240973 | +0.000002 |

LPIPS uses the exact final RGB8 PPM pairs, official package, AlexNet backbone,
version 0.1, and `[-1, 1]` input normalization.

| Scene | LPIPS dev.26 | LPIPS dev.27 | Delta | Median | p95 | Maximum |
|---|---:|---:|---:|---:|---:|---:|
| GAJAN | 1.046475 | 1.047886 | +0.001411 | 1.041031 | 1.203848 | 1.230240 |
| Savères | 1.201670 | 1.201634 | -0.000036 | 1.206376 | 1.234318 | 1.243844 |
| Albagnac | 1.085614 | 1.085546 | -0.000068 | 1.071063 | 1.296707 | 1.422777 |

The large-scene differences are numerical noise at this bounded budget. GAJAN
is more sensitive because six MRNF refinements amplify small floating-point
differences; it still improves PSNR and ends only two splats below the
reference.

## Throughput and topology

| Scene | Train dev.26 | Train dev.27 | Delta | Wall dev.26 | Wall dev.27 | Delta |
|---|---:|---:|---:|---:|---:|---:|
| GAJAN | 14.125 s | 13.309 s | -5.8% | 16.786 s | 14.178 s | -15.5% |
| Savères | 53.125 s | 58.247 s | +9.6% | 62.492 s | 62.765 s | +0.4% |
| Albagnac | 75.456 s | 82.987 s | +10.0% | 83.941 s | 88.957 s | +6.0% |

| Scene | Added | Pruned | Final Gaussians | PLY bytes |
|---|---:|---:|---:|---:|
| GAJAN | 4,667 | 3 | 13,988 | 3,302,735 |
| Savères | 44,950 | 1 | 687,110 | 162,159,528 |
| Albagnac | 71,743 | 7 | 1,096,829 | 258,853,213 |

Savères and Albagnac topology and PLY byte size exactly match dev.26. The
remaining large-scene compute regression must be profiled by kernel before
claiming a speed improvement; it is not hidden by the faster GAJAN result.

## Rejected implementation candidates

- Compact source-index sort with indirect original-record access linked
  sm_89 and saved the second record buffer, but reached 64.77 s on Savères
  and 91.79 s on Albagnac. Random render/backward record reads were rejected.
- Eliminating the projected sort entirely reduced GAJAN training to 12.94 s,
  but Savères regressed to 65.75 s wall time because tile rendering lost
  depth-order record locality.
- Gathering SH bases into sorted order changed Savères training by only
  +0.001 s versus the retained direct48 candidate while reserving about
  44 MiB more at its cap. It was rejected.

These rejected outputs remain benchmark artifacts only and are not the
versioned implementation.

## Build and automated evidence

- Clean default CMake architecture: `89-real;89-virtual`.
- `cuobjdump --list-elf`: `dronegs.1.sm_89.cubin`.
- Six of six suites pass: core, CPU rasterization, CUDA loss, CUDA
  rasterization, CUDA training, and dependency-free LPIPS tool tests.
- Equal-depth stability and CPU/CUDA forward/backward parity remain covered.

## License boundary

The modified rasterization path is in
`app1-colmap/dronegs/cuda/rasterization.cu`, already conservatively marked
GPL-3.0-or-later because it also contains the MRNF-derived lifecycle work.
The CMake default, public source-index width guard, CLI/version text,
documentation, manifest, and LPIPS evaluator remain under their existing
licenses. Binaries linking the GPL CUDA translation units remain GPL-covered.

## Remaining gates

1. Add checkpoint/resume before long 1,000+ image convergence runs.
2. Profile per-kernel sm_52-PTX versus sm_89 on Savères and Albagnac; target
   the measured large-scene regression rather than architecture flags.
3. Run sufficiently long same-view DroneGS and pinned LichtFeld controls.
4. Profile/fuse host-mediated topology compaction before many-refinement
   throughput claims.
5. Complete visual and downstream orthomosaic/detection non-regression.
