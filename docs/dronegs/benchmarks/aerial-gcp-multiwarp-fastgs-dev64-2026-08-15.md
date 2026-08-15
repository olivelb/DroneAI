# DroneGS dev.64 multiwarp FastGS backward qualification

Date: 2026-08-15

Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB (`sm_86`); local RTX 4070 Laptop
8 GiB (`sm_89`) for the second native CUDA target

Status: promoted; repeated short gate, native CUDA portability checks and two
exact-commit 30,000-step HQ runs passed

## Purpose and invariants

The structural FastGS backward kernel historically launched one 32-thread
block for every independent bucket. That shape limits an RTX 3090 to 16
resident warps per SM because the architectural resident-block limit is
reached before registers or shared memory are exhausted.

Dev.64 keeps one 32-thread NVIDIA warp responsible for exactly one bucket, but
places four independent bucket warps in each 128-thread CUDA block. Every warp
has disjoint shared checkpoint state. Bucket traversal, contribution order,
derivative equations and atomic accumulation are unchanged. The implementation
uses standard CUDA warp operations and contains no architecture-specific
intrinsic or hard-coded SM branch.

The `sm_86` kernel uses 78 registers per thread and 4,096 bytes of shared
memory per block, versus 77 registers and 1,024 bytes for the one-warp
reference. Its theoretical residency rises from 16 to 24 warps per SM. The
change adds no persistent VRAM allocation and does not change checkpoint or
PLY formats.

## Build and CUDA portability

The exact source commit `5771733f06288f656c49f957839b039311939de9` was
checked out cleanly on BIGZEN. Its Release `sm_86` binary SHA-256 is
`dc207a4520e02749f58cf5e4e53aae8934e544ef5d8f7997f304a8eefdc235af`.
All eight native CPU/CUDA CTests passed on the RTX 3090 with CUDA 12.0.

The same source was built and all eight CTests passed on the local RTX 4070
Laptop (`sm_89`) with CUDA 12.9. A portable fat-binary build also completed
for the repository target set and contains cubins for `sm_75`, `sm_80`,
`sm_86`, `sm_87`, `sm_89`, `sm_90`, `sm_100`, `sm_101` and `sm_120`.
Targets other than `sm_86` and `sm_89` are compile-qualified only; performance
claims remain specific to the measured RTX 3090.

Nsight Compute 2022.4.1 cannot profile this GPU through the installed WSL
stack (`Profiling is not supported on device 0 as it uses WSL`). The attempted
one-step run completed normally, but produced no kernel profile. Qualification
therefore uses compiler resource reports, native CTests and the trainer's CUDA
event stage telemetry.

## Controlled short gate

The dev.63 reference and dev.64 candidate use the same aerial-GCP cell,
initial 5.1 M PLY, seed 42, frozen split, 1,000 fixed-topology iterations, SH3
and no checkpoint. Each exact binary was run twice.

| Build | Run | Training (s) | Wall (s) | PSNR (dB) | SSIM | Pixel-weighted PSNR | Pixel-weighted SSIM |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev.63 | A | 34.9300 | 55.9060 | 21.87941 | 0.482450 | 21.14916 | 0.497885 |
| dev.63 | B | 34.8943 | 55.2972 | 21.88201 | 0.482431 | 21.14871 | 0.497812 |
| dev.64 | A | 34.0119 | 54.4583 | 21.88083 | 0.482510 | 21.14762 | 0.497888 |
| dev.64 | B | 34.0447 | 54.6318 | 21.88173 | 0.482387 | 21.14778 | 0.497800 |

Mean changes from dev.63 to dev.64:

- native training: 34.9121 to 34.0283 seconds, `-2.53%`;
- wall: 55.6016 to 54.5451 seconds, `-1.90%`;
- PSNR: `+0.00057 dB`;
- SSIM: `+0.000008`;
- pixel-weighted PSNR: `-0.00124 dB`;
- pixel-weighted SSIM: `-0.000005`;
- final loss: `-0.0000054`;
- population: unchanged at 5,100,000 Gaussians.

At iteration 999, raster backward falls from 8.357/8.283 ms to
7.273/7.333 ms, about `-12%`.

## Exact-commit 30,000-step HQ gate

Two uninterrupted runs started from iteration zero with the exact dev.64
binary. Both completed 30,000 steps, reached 5.1 M Gaussians, wrote exactly
three checkpoints and exited without OOM, partial product or trainer error.
The frozen dev.63 HQ result is the direct reference.

| Metric | dev.63 HQ | dev.64 HQ A | dev.64 HQ B | dev.64 mean | Mean delta |
|---|---:|---:|---:|---:|---:|
| Native training | 860.926 s | 848.454 s | 850.393 s | 849.424 s | **-1.34%** |
| Wall time | 871.779 s | 859.489 s | 861.139 s | 860.314 s | **-1.32%** |
| Final loss | 0.0228477 | 0.0226516 | 0.0228731 | 0.0227624 | -0.0000853 |
| Mean PSNR | 21.8885 dB | 21.8719 dB | 21.9071 dB | 21.8895 dB | +0.0010 dB |
| Mean SSIM | 0.481737 | 0.478939 | 0.481299 | 0.480119 | -0.001618 |
| Pixel-weighted PSNR | 21.1522 dB | 21.1756 dB | 21.1949 dB | 21.1852 dB | +0.0331 dB |
| Pixel-weighted SSIM | 0.497536 | 0.498042 | 0.498061 | 0.498052 | +0.000516 |
| Final Gaussians | 5,100,000 | 5,100,000 | 5,100,000 | 5,100,000 | 0 |
| Checkpoints | 3 | 3 | 3 | 3 | 0 |

Run A alone is `-0.002798` below the dev.63 arithmetic-mean SSIM. Run B is
only `-0.000438` below it, and the two-run candidate mean is `-0.001618` below
the reference. Both the repeat and the repeated mean therefore pass the
existing `0.002` non-regression envelope. The operator-authorized fallback to
a rounded `0.003` limit was not needed and the repository gate remains
unchanged.

The arithmetic variation is concentrated in a narrow held-out vegetation
view. Pixel-weighted SSIM improves in both runs. Visual comparison of that
view against the target and dev.63 found no new hole, tear, seam, support loss
or structural blur; only fine stochastic vegetation texture differs.

At iteration 29,999 in run B, raster backward is 7.470 ms, scalar Adam is
6.003 ms, SH Adam is 10.992 ms and the complete sampled GPU step is 34.834 ms.

## Retained evidence

- Short A/B directory:
  `/home/olivier/benchmarks/dronegs-fastgs-multiwarp-dev64-20260815`.
- HQ directories:
  `/home/olivier/benchmarks/aerial-gcp-hq-multiwarp-fastgs-dev64-5771733-20260815/cell0`
  and `cell0-r2`.
- Run A manifest / PLY / evaluation CSV SHA-256:
  `10763a187e7845780edacaedeaace795cb58413e3d364f500c3f8fded2b9fff9`,
  `d6102b3e43b869222b29efbad861cfaf45f5ba7ce13014144ef9a9f29f3f3863`,
  `288efa920432accbd921a6a0584b340c9a609b539dbbc10786ef40d0a9e1296f`.
- Run B manifest / PLY / evaluation CSV SHA-256:
  `d2135785b0602c9357e3c709279bb81aff519eba2c47952749d62c4bc5007a97`,
  `a324e2f78a3b4f7407d3dfa3fa51c62ac9048384e1d53de5c737f73cc8874515`,
  `e88afa9e258d72d76cb3fb141c3e005955ebad2b948fd1ec6b4456b5afa0af82`.

No qualification artifact was deleted.

## Decision

Promote dev.64 without changing the quality threshold. The portable source,
two native GPU architectures, portable target build, repeated short A/B,
two exact-commit HQ runs, artifact integrity and visual gate all pass. The
measured RTX 3090 gain is modest but repeatable and quality-neutral inside the
established long-run envelope.
