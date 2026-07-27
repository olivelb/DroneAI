# Phase 4 MRNF growth isolation result

Date: 2026-07-25
Status: population gate passed; quality and speed gates failed

## Controlled scope

DroneGS `0.5.0-dev.15` adds one MRNF subset to dev.14:

- normalized per-pixel `1-SSIM` error map;
- per-Gaussian alpha-contribution and error-weighted contribution statistics;
- maximum error weight accumulated over 200-step windows;
- candidate threshold `0.003`;
- deterministic descending weight/source-index selection;
- 7% growth until iteration 15,000, bounded by `max_cap`;
- rotated longest-axis parent/child split;
- reset Adam moments for every selected parent and appended child.

Dataset, split, image targets, renderer, L1+DSSIM loss, fixed SH degree 0,
parameter schedules, seed, and held-out protocol are unchanged from dev.14.
Prune/replacement, Gumbel sampling, edge guidance, noise injection, strategy
decay, compaction, and progressive SH remain out of scope.

## GPL boundary

The error weighting, cadence, threshold/fraction, and long-axis split behavior
were adapted after inspecting pinned LichtFeld GPL sources at revision
`1004c0841a3776e3f67866ff34101fbc9677397f`.

The affected combined translation units are marked GPL-3.0-or-later:

```text
app1-colmap/dronegs/cuda/rasterization.cu
app1-colmap/dronegs/cuda/trainer.cu
```

Exact upstream files and redistribution treatment are recorded in
`GPL_COMPONENTS.md`. Other original DroneGS units retain their MIT
identifiers; the linked dev.15 native binary is GPL-covered.

## Correctness and smoke gates

All five direct native test executables pass on the RTX 4070 Laptop. The new
test forces a one-to-two split and verifies count/capacity, rotated long-axis
distance, matched parent/child scales, DC and opacity, and statistic reset.

The first 200-step smoke used screen-gradient magnitude and selected only four
candidates, proving that this statistic was not compatible with the active
LichtFeld error-map path. It is preserved at:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev15-growth-smoke-200/
```

After correcting the statistic to normalized SSIM-error-weighted alpha
contribution, the second smoke selected 1,024,723 candidates and added 71,731
Gaussians:

```text
/home/olivier/droneAI-workspaces/albagnac-dronegs-dev15-growth-smoke-200-b/
```

## Albagnac protocol

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Fingerprint | `fnv1a64:b52de467fbfc898e` |
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Resolution | 800 x 580 |
| Iterations / seed | 500 / 42 |
| Initial Gaussians | 1,025,093 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev15-mrnf-growth-heldout-500/` |

## Growth result

| Iteration | Candidates | Added | Population |
|---:|---:|---:|---:|
| 200 | 1,024,922 | 71,745 | 1,096,838 |
| 400 | 1,096,253 | 76,738 | 1,173,576 |

DroneGS adds 148,483 Gaussians. Pinned LichtFeld adds 148,447 and ends at
1,173,540. The final difference is only 36 Gaussians, or approximately
0.0031% of the control population.

## Held-out quality

| Metric | Initial | dev.14 final | dev.15 final | dev.15 vs dev.14 |
|---|---:|---:|---:|---:|
| Mean PSNR | 14.063148 dB | 17.115355 dB | 17.059732 dB | -0.055622 dB |
| Median PSNR | 14.136738 dB | 17.082636 dB | 17.052894 dB | -0.029742 dB |
| Mean SSIM | 0.181077 | 0.246278 | 0.244958 | -0.001321 |
| Median SSIM | 0.170659 | 0.221484 | 0.220485 | -0.000999 |
| Mean active coverage | 0.996239 | 0.999872 | 0.999934 | +0.000062 |

Against dev.14, PSNR improves on 45 views and regresses on 127; SSIM improves
on 32 and regresses on 140. Against initialization, dev.15 still improves
PSNR on 171 of 172 views and SSIM on all 172.

## Timing and artifacts

| Item | dev.14 | dev.15 | Change |
|---|---:|---:|---:|
| Trainer compute | 41.052 s | 55.865 s | +36.1% |
| Initial + final evaluation | 17.289 s | 17.757 s | +2.7% |
| Native manifest wall | 60.489 s | 75.417 s | +24.7% |
| Measured process wall | 60.56 s | 75.50 s | +24.7% |
| Maximum process RSS | 531,800 KiB | 593,776 KiB | +11.7% |
| Final PPM files / bytes | 172 / 239,426,580 | 172 / 239,426,580 | equal |

No CUDA or host OOM occurred. Peak VRAM was not sampled during this run.

Final PLY:

```text
bytes: 65,720,710
sha256: 54e62c095ea0f408d962e95ff806b66d4bd7e5bb031947281a9c10dab2fa7e6d
```

## Pinned LichtFeld comparison

| Metric | DroneGS dev.15 | Pinned LichtFeld | DroneGS gap |
|---|---:|---:|---:|
| Held-out PSNR | 17.059732 dB | 21.068552 dB | -4.008820 dB |
| Held-out SSIM | 0.244958 | 0.631048 | -0.386090 |
| Final Gaussians | 1,173,576 | 1,173,540 | +36 |
| Trainer/application time | 55.865 s | 51.962 s | not directly comparable |
| Full measured wall | 75.50 s | 80.26 s | not directly comparable |

Timing remains diagnostic because the LichtFeld container also writes PNG and
a 209 MB resume checkpoint and performs a different evaluation sequence.

## Decision

Accept dev.15 as a reproducible MRNF population/capacity correctness slice.
Reject deterministic population growth as a quality improvement: count parity
is achieved, but held-out quality and trainer compute both regress.

Do not tag `dronegs-v0.5.0`. The next controlled experiment should add
weighted stochastic selection and edge guidance before altering split
geometry again. Strategy-specific learning-rate/decay parity is the following
candidate. Progressive SH cannot explain this SH-degree-0 control.
