# Rejected coefficient-centric SH Adam experiment

Date: 2026-08-15

Hardware: BIGZEN, NVIDIA RTX 3090 24 GiB (`sm_86`)

Status: rejected and reverted; evidence retained

## Hypothesis

Experimental commit `c72663401d7de4183640978cac131d36d7fcbb81`
assigned one active SH coefficient to each CUDA lane. The lane updated the
three colour parameters and matching opacity-SH parameter together, reusing
the projected basis value and a power-of-two Gaussian index. Equations,
moments, parameter order, persistent VRAM and checkpoint format v5 were
unchanged.

The exact Release binary SHA-256 was
`a186a15399f9619a8d36cffceaa65604632f73c167586c2c68eb5de91abb9d29`.
All eight native CPU/CUDA CTests passed on the RTX 3090.

## Repeated fixed-topology gate

The experiment and dev.64 reference used the same aerial-GCP cell, initial
5.1 M PLY, seed 42, frozen split, SH3 and 1,000 fixed-topology iterations.

| Metric | dev.64 mean | Experiment mean | Delta |
|---|---:|---:|---:|
| Native training | 34.0283 s | 33.7718 s | **-0.75%** |
| Mean PSNR | 21.88128 dB | 21.88288 dB | +0.00160 dB |
| Mean SSIM | 0.482448 | 0.482414 | -0.000034 |
| Pixel-weighted PSNR | 21.14770 dB | 21.14898 dB | +0.00128 dB |
| Pixel-weighted SSIM | 0.497844 | 0.497833 | -0.000010 |
| Final Gaussians | 5,100,000 | 5,100,000 | 0 |

Late SH Adam falls from about 10.99 to 10.79 ms (`-1.9%`), but the complete
training gain remains below one percent.

## Two-run 30,000-step HQ gate

Both uninterrupted experimental runs started from iteration zero, reached
5.1 M Gaussians, wrote exactly three checkpoints and exited without OOM,
partial product or trainer error.

| Metric | dev.64 HQ mean | Experiment HQ A | Experiment HQ B | Experiment mean | Mean delta |
|---|---:|---:|---:|---:|---:|
| Native training | 849.424 s | 850.406 s | 854.835 s | 852.621 s | **+0.38%** |
| Final loss | 0.0227624 | 0.0229605 | 0.0227086 | 0.0228346 | +0.0000722 |
| Mean PSNR | 21.8895 dB | 21.8540 dB | 21.8718 dB | 21.8629 dB | -0.0266 dB |
| Mean SSIM | 0.480119 | 0.478534 | 0.480707 | 0.479621 | -0.000498 |
| Pixel-weighted PSNR | 21.1852 dB | 21.1664 dB | 21.1910 dB | 21.1787 dB | -0.0065 dB |
| Pixel-weighted SSIM | 0.498052 | 0.496577 | 0.498391 | 0.497484 | -0.000568 |
| Final Gaussians | 5,100,000 | 5,100,000 | 5,100,000 | 5,100,000 | 0 |

The two-run quality mean remains inside the unchanged `0.002` SSIM
non-regression envelope relative to dev.64. No quality-threshold adjustment is
required. Late SH Adam improves by about `1.70%`, but full HQ training becomes
`0.38%` slower because the local saving is smaller than normal topology and
stage variation.

## Evidence

- Fixed-topology directory:
  `/home/olivier/benchmarks/dronegs-coefficient-sh-adam-dev65-20260815`.
- HQ directory:
  `/home/olivier/benchmarks/aerial-gcp-hq-coefficient-sh-adam-dev65-c726634-20260815`.
- HQ A manifest / PLY / evaluation CSV SHA-256:
  `c4386b3660bfd453804b0586a8df6c3e27b2ca80caec9c72bed54ed0db0392da`,
  `c3ebf2de94d6115c062deeb51b1ab20f547766dfa2301b931221ac3bb5a116bc`,
  `5ecb951936c2c488802848fce9bbff7a8acde21ca9b0d9de1f10caa7f5b9efff`.
- HQ B manifest / PLY / evaluation CSV SHA-256:
  `1947d26a10c66e941956f0dbabe783bd1015d4a368195ad976d88bfd9cc9b2d3`,
  `3577fe70b4f63fb5ae190f5e765778360baf7cc20930ebc2e135fd9d76222886`,
  `135ab141eab43c149443b03c22117e3908b4f264950cfc83aa1a90f03486f7a6`.

No benchmark artifact was deleted.

## Decision

Reject and revert the implementation. It does not meet the operator's minimum
`2%` end-to-end improvement rule and does not provide enough architectural
simplification to justify an exception. Dev.64 remains the production source
and quality threshold `0.002` remains unchanged.
