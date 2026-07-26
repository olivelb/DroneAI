# Phase 4 — compensated anti-aliasing ablation (dev37, 2026-07-26)

## Question

Can a coherent screen-space reconstruction filter improve the remaining
LichtFeld quality gap without an extra render pass or architecture-specific
code?

Dev37 adds a variance to both diagonal entries of the clamped projected
covariance and multiplies opacity by:

```text
sqrt(det(original covariance) / det(filtered covariance))
```

The backward pass includes both the inverse-covariance derivative and the
analytic determinant-compensation derivative.  The implementation is an
independent adaptation after inspecting the pinned Apache-2.0 gsplat revision
recorded in `GPL_COMPONENTS.md`; no gsplat source is linked or vendored.

## Protocol

- Albagnac existing dense COLMAP product, read-only
- 500 iterations, seed 42, SH0, MRNF, resize factor 4
- dev36 AbsGrad 0.50 and staged rotation 0.008 base
- filter variances 0.05, 0.15, and 0.30 pixel squared
- exact held-out PSNR/SSIM and exact-pair AlexNet LPIPS

## Results

| Profile | PSNR | SSIM | LPIPS |
|---|---:|---:|---:|
| dev36, no filter | 18.282480 | 0.371775 | **0.678306** |
| dev37, 0.05 | 18.288942 | 0.372448 | 0.682513 |
| dev37, 0.15 | 18.304497 | **0.372670** | 0.693540 |
| dev37, 0.30 | **18.318575** | 0.372391 | 0.710590 |

The filter gives a monotonic PSNR gain up to 0.30 and a small SSIM gain, but
all tested strengths regress LPIPS.  DroneGS already clamps the minimum
projected eigenvalue to 0.5625 pixel squared, so the common 0.30 gsplat filter
is additive to an already broad reconstruction footprint.

## Decision

Retain the three profiles as architecture-independent, opt-in metric/rending
ablations.  Do not promote any of them as the balanced quality profile and do
not combine them with further convergence experiments by default.

The large remaining LichtFeld gap is not explained by anti-aliasing.  The next
causal gate is MRNF split parity: LichtFeld uses two rotated stochastic normal
offsets, uniform scale division by 1.6, and revised opacity, whereas DroneGS
currently uses a deterministic long-axis displacement, anisotropic shrink,
and a 0.6 opacity factor.
