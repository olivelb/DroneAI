# Phase 4 — AbsGrad-guided MRNF (dev36, 2026-07-26)

## Hypothesis

The signed screen-space mean gradients used by the original MRNF score can
cancel across pixels.  Dev36 accumulates the absolute x/y contribution before
the pixel reduction, averages it over visible training views, normalizes it by
the positive median, and uses it only as a multiplier of the existing
SSIM-error/edge growth score.

The implementation is an independent adaptation of the AbsGS idea.  No source
from gsplat or AbsGS is linked or vendored.  The exact gsplat revision inspected
during the design audit is recorded in `GPL_COMPONENTS.md`.

## Protocol

- CUDA build: `dronegs-dev36-absgrad`
- seed: 42
- strategy: MRNF
- profile base: dev35 staged rotation 0.008
- tested AbsGrad weights: 0.25 and 0.50
- exact held-out PSNR/SSIM and external AlexNet LPIPS
- existing COLMAP dense products only; no COLMAP or bundle adjustment rerun

## Results

| Scene / budget | Profile | PSNR | SSIM | LPIPS |
|---|---:|---:|---:|---:|
| Albagnac / 500 | dev35 rotation 0.008 | 18.286057 | 0.371625 | 0.679227 |
| Albagnac / 500 | dev36 AbsGrad 0.25 | 18.284884 | 0.371278 | 0.679660 |
| Albagnac / 500 | dev36 AbsGrad 0.50 | 18.282480 | 0.371775 | **0.678306** |
| GAJAN / 1200 | dev35 rotation 0.008 | 14.390007 | 0.285297 | **0.875988** |
| GAJAN / 1200 | dev36 AbsGrad 0.50 | **14.507236** | **0.286847** | 0.876368 |
| Savères / 1000 | dev35 rotation 0.008 | 17.759613 | 0.337739 | 0.739060 |
| Savères / 1000 | dev36 AbsGrad 0.50 | **17.765158** | **0.337950** | **0.737264** |

Albagnac training time for weight 0.50 was 9.19 s and wall time was 52.40 s,
versus 9.06 s and 51.00 s for dev35.  The additional persistent state is two
floats plus one observation counter per Gaussian, with a two-float temporary
per-frame buffer.

## Decision

Keep weight 0.50 as an opt-in, architecture-independent dev36 profile.  It is a
small but cross-scene useful improvement: better perceptual quality on the two
large drone scenes and materially better PSNR/SSIM on GAJAN.  It does not close
the LichtFeld quality gap by itself.

The next quality experiment must change the reconstruction filter rather than
only the densification ranking.  Dev37 will test a coherent anti-aliased
projection with the matching energy compensation, as an ablation before any
larger MCMC-style topology rewrite.
