# Phase 4: FastGS projected-covariance transplant — no-go

Date: 2026-07-26
Base version: `0.5.0-dev.32`
Disposition: rejected; no implementation retained

> Superseded by dev.38 for the broader hypothesis. The covariance-only patch
> remains correctly rejected; dev.38 succeeds only after covariance, FOV
> clamping, opacity-dependent support, alpha ceiling, and backward behavior
> are changed together. See
> `phase4-fastgs-compatibility-parity-dev38-2026-07-26.md`.

## Hypothesis

After local-KNN initialization, DroneGS and the pinned LichtFeld/FastGS
reference still differed in their projected covariance regularization.
DroneGS used bounded spectral variance while FastGS adds a fixed pixel-space
low-pass term and does not apply the same maximum variance clamp.

An isolated experimental patch replaced the spectral variance clamp
`[0.5625,64]` by the FastGS-style additive `0.3 I` covariance term and updated
the analytical backward derivative. It was intentionally evaluated outside
the canonical branch.

The adapted equations were inspected from the pinned GPL-3.0-or-later
LichtFeld/FastGS sources. Because the candidate was rejected, no copied or
adapted covariance code was merged.

## Checks

- all six native test suites passed;
- covariance finite-difference and CPU/CUDA parity remained valid;
- the initial Albagnac held-out render improved from approximately
  `8.0293 dB / 0.096715 SSIM` to `11.9944 dB / 0.189926 SSIM`.

The better initial render did not translate into stable optimization.

## Albagnac 500-step result

| Metric | dev.32 accepted | covariance transplant |
|---|---:|---:|
| PSNR | 17.032278 dB | 11.991917 dB |
| SSIM | 0.340845 | 0.193172 |
| Final loss | lower/converged | 0.421168 |
| Final Gaussians | 1,172,452 | 1,150,305 |
| Training | 9.373 s | 25.065 s |
| Wall | 50.734 s | 54.598 s |

## Conclusion

No-go. FastGS covariance behavior is coupled to its projected bounds,
overlap policy, alpha/compositing thresholds, and early-exit behavior. Moving
the covariance term alone makes the initial footprint look closer to
LichtFeld, but produces poor gradients and fails to converge under the
DroneGS renderer.

The canonical dev.32 spectral bounds remain unchanged. A future FastGS
compatibility experiment must treat covariance, bounds, overlap, and
composition as one gated renderer variant rather than transplanting one
formula. Opacity calibration and longer same-view controls have higher
priority and lower implementation risk.

## Experimental artifacts

- build:
  `/home/olivier/droneAI-workspaces/builds/dronegs-dev33-covariance`
- run:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev33-covariance-dc010-500`
