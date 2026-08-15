# DroneGS dev.63 interleaved SH Adam qualification

Date: 2026-08-15

Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB

Status: short repeated gate passed; exact-commit 30,000-step HQ gate pending

## Purpose and invariants

At dev.62's 5.1 M resident population, SH Adam is the largest measured
optimizer stage at about 12.49 ms per step. Dev.63 makes two internal changes:

- compile-time specialization for the only valid active coefficient counts,
  3, 8 and 15;
- one `float2` allocation per colour-SH or opacity-SH parameter, containing
  its first and second Adam moments.

Every active parameter still has one CUDA work item. The update equation,
coefficient order, learning rates, bias correction and epsilon remain exact.
Interleaving replaces two scalar moment loads/stores with one vector
load/store and reduces SH3 register use from 26 to 20. Runtime moment bytes do
not change.

Checkpoint format v5 is unchanged. The in-memory snapshot retains interleaved
moments directly; its writer emits the historical first-moment array followed
by the second-moment array in bounded chunks. Reload retains one scalar moment
array and converts the second in bounded chunks before offset device copies,
avoiding multiple full-size conversion buffers.

## Rejected controls

The following short controls are retained on BIGZEN but excluded from the
production implementation except where noted:

- power-of-two 16/32/64-lane padding: `-0.41%` mean native training and no
  wall-time gain; rejected because the four idle SH3 lanes were not justified;
- exact-lane compile-time specialization alone: `-0.70%` mean native
  training; retained as the first half of dev.63;
- `restrict` annotations: no additional native-training gain; rejected;
- approximate `rsqrtf` Adam denominator: no measurable kernel gain and a less
  exact numerical contract; rejected.

Evidence directories are respectively:

- `/home/olivier/benchmarks/dronegs-specialized-sh-layout-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-sh-exact-lanes-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-restrict-sh-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-rsqrt-sh-dev63-20260815`.

## Controlled short gate

The dev.62 reference and dev.63 candidates use the same aerial-GCP cell,
initial 5.1 M PLY, seed, frozen split, 1,000 fixed-topology iterations and no
checkpoint. Each build was run twice.

| Build | Run | Training (s) | Wall (s) | PSNR (dB) | SSIM | Pixel-weighted PSNR | Pixel-weighted SSIM |
|---|---:|---:|---:|---:|---:|---:|---:|
| dev.62 | A | 35.7569 | 55.8029 | 21.86098 | 0.481537 | 21.16789 | 0.499019 |
| dev.62 | B | 35.7810 | 55.9740 | 21.86170 | 0.481590 | 21.16912 | 0.499059 |
| dev.63 | A | 34.2204 | 59.5514 | 21.85925 | 0.481531 | 21.16716 | 0.499048 |
| dev.63 | B | 34.1864 | 54.0017 | 21.86086 | 0.481319 | 21.16761 | 0.498835 |

Mean changes from dev.62 to dev.63:

- native training: 35.7690 to 34.2034 seconds, `-4.38%`;
- wall: 55.8885 to 56.7766 seconds, `+1.59%`, dominated by a cold-load
  outlier in candidate A; candidate B wall is 54.0017 seconds;
- PSNR: `-0.00129 dB`;
- SSIM: `-0.000139`;
- pixel-weighted PSNR: `-0.00112 dB`;
- pixel-weighted SSIM: `-0.000098`;
- final loss: `+0.0000022`;
- population: unchanged at 5,100,000 Gaussians.

At iteration 999, both candidates measure SH Adam at 10.97-10.99 ms, versus
about 12.49 ms in dev.62 (`~ -12%`). Scalar Adam remains about 5.96 ms.

## Verification and retained evidence

- Native CPU/CUDA CTests: `8/8` passed after the final checkpoint-memory
  implementation.
- Dev.63 manifests:
  `f1decbdcbccb72f8c36ad521fc544e9c0fcb320f0eba5dba289fc8e6952c1110`
  and
  `310a48e3052d59d648a1e003502d54e14b5abb028d62797df9b20a909256c23d`.
- Candidate directory:
  `/home/olivier/benchmarks/dronegs-interleaved-sh-moments-dev63-20260815`.
- The retained 4.4 GiB dev.62 HQ checkpoint passed checksum, format,
  configuration and optimizer-state loading with the streamed dev.63 loader.
  The CLI then produced the expected terminal diagnostic because that
  checkpoint already represents iteration 30,000 of a 30,000-step run.
- No benchmark artifact was removed.

## Promotion gate

Build a clean checkout of the committed dev.63 source, rerun all eight native
CPU/CUDA CTests, then execute the established 30,000-step HQ cell at the 5.1 M
cap with three checkpoints. Promote only if the manifest and all artifacts
validate and held-out quality remains inside the established envelope against
dev.62.
