# DroneGS dev.63 interleaved SH Adam qualification

Date: 2026-08-15

Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB

Status: promoted; short repeated and exact-commit 30,000-step HQ gates passed

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

The original evidence directories were respectively:

- `/home/olivier/benchmarks/dronegs-specialized-sh-layout-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-sh-exact-lanes-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-restrict-sh-dev63-20260815`;
- `/home/olivier/benchmarks/dronegs-specialized-rsqrt-sh-dev63-20260815`.

These superseded short-run artifacts were removed from BIGZEN at operator
request after their metrics and decisions had been recorded here.

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

## Exact-commit 30,000-step HQ gate

The exact commit `79367070cfcf6086266948ee20a3214e46557730` was checked out
cleanly on BIGZEN and built in Release mode for the RTX 3090. All eight native
CPU/CUDA CTests passed. The binary SHA-256 is
`29874520bc259ef5fd2dff0357830bc88ac33acf7d5934ef9845a305bab332db`.

An initial attempt was lost when the BIGZEN host crashed. Its artifacts were
removed and the promoted result below is a new uninterrupted run from
iteration zero, not a resumed or combined timing. It completed all 30,000
steps, reached 5.1 M Gaussians, wrote exactly three checkpoints and exited
with code zero.

| Metric | dev.62 HQ | dev.63 HQ | Delta |
|---|---:|---:|---:|
| Native training | 884.450 s | 860.926 s | **-2.66%** |
| Wall time | 896.056 s | 871.779 s | **-2.71%** |
| Final loss | 0.0228701 | 0.0228477 | -0.0000224 |
| Mean PSNR | 21.8412 dB | 21.8885 dB | +0.0473 dB |
| Mean SSIM | 0.480750 | 0.481737 | +0.000987 |
| Pixel-weighted PSNR | 21.1349 dB | 21.1522 dB | +0.0172 dB |
| Pixel-weighted SSIM | 0.497699 | 0.497536 | -0.000162 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |
| Checkpoints | 3 | 3 | 0 |

At iteration 29,999, SH Adam takes 10.989 ms versus about 12.491 ms on
dev.62 (`-12.0%`). Scalar Adam is effectively unchanged at 5.997 ms versus
5.972 ms. The complete GPU step takes 35.972 ms versus 37.181 ms (`-3.25%`).
Observed resident VRAM remained about 8.33 GiB.

Visual inspection of held-out view 2 against the target and dev.62 prediction
found equivalent support and texture, with no new hole, tear, boundary or blur
artifact. The aggregate PSNR and SSIM improvements and the `-0.000162`
pixel-weighted SSIM delta remain comfortably inside the established `0.002`
long-run non-regression envelope.

## Verification and retained evidence

- Native CPU/CUDA CTests: `8/8` passed from the clean exact-commit Release
  build.
- Dev.63 manifests:
  `f1decbdcbccb72f8c36ad521fc544e9c0fcb320f0eba5dba289fc8e6952c1110`
  and
  `310a48e3052d59d648a1e003502d54e14b5abb028d62797df9b20a909256c23d`.
- Promoted HQ directory:
  `/home/olivier/benchmarks/aerial-gcp-hq-interleaved-sh-adam-dev63-7936707-20260815/cell0`.
- The retained 4.4 GiB dev.62 HQ checkpoint passed checksum, format,
  configuration and optimizer-state loading with the streamed dev.63 loader.
  The CLI then produced the expected terminal diagnostic because that
  checkpoint already represents iteration 30,000 of a 30,000-step run. The
  checkpoint was later removed during the operator-requested cleanup.
- Final manifest SHA-256:
  `3aee7095d2cabf093b563b1c7820b7d8f10a58df57b91b71bc056551fa7bc2fe`.
- Final PLY SHA-256:
  `57f6133309a23f18aa10ffc8c4589cb1c8a109b8274818c916b3f995a98ed8e5`.
- Evaluation CSV SHA-256:
  `8039e54ebb33338948173838305b2993c9503f221ec5c9054123a9508efbb153`.
- No temporary checkpoint or partial product remains.

## Decision

Promote dev.63. The repeated short gate, exact-commit build and CTests,
checkpoint compatibility, uninterrupted HQ timing, artifact validation and
held-out quality gates all pass.
