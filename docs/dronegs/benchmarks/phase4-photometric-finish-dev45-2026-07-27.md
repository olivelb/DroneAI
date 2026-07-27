# Dev.45 progressive photometric finish

Date: 2026-07-27
Status: implementation accepted; full 15k validation approved

## Question

Can DroneGS convert part of its SSIM/LPIPS advantage over LichtFeld into the
remaining PSNR gap without adding iterations or slowing the training kernel?

Dev.45 adds two opt-in controls:

- `--photometric-finish N`;
- `--photometric-mse-percent P`.

During the final `N` iterations, the analytical image gradient transitions
linearly from `0.8 active-pixel L1 + 0.2 DSSIM` to a blend whose final
active-pixel MSE weight is `P%`. Both defaults are zero. Per-step loss
telemetry deliberately remains the baseline L1+DSSIM value so convergence
curves remain comparable.

## Validation

- Release CUDA portable build completed for Turing through Blackwell.
- Six of six C++/CUDA/Python suites pass.
- The structural FastGS mixed objective and image gradient pass CPU reference
  parity and four central finite-difference probes at 50% MSE.
- Albagnac dense COLMAP data and source PLY models remained read-only.
- All 4,000-step candidates used seed 42, progressive SH3, resize factor 4,
  1,600 maximum width, the 1.5 million cap, modulo-8 split,
  LichtFeld-absolute rates, LichtFeld bounds, structural FastGS, and a
  1,000-step topology cooldown.

## Quality ablation

| Candidate | Training | Held-out PSNR | Held-out SSIM | Baseline loss |
|---|---:|---:|---:|---:|
| Dev.44 cooldown 1,000 | 173.554 s | 21.322853 dB | 0.596799 | 0.170790 |
| Dev.45 final MSE 25% | 204.204 s* | 21.353636 dB | 0.596637 | 0.170496 |
| Dev.45 final MSE 50% | 211.160 s* | 21.389217 dB | 0.596099 | 0.169933 |
| Dev.45 final MSE 100% | 216.172 s* | **21.490055 dB** | 0.592110 | **0.169752** |

`*` These full-pilot times were contaminated by different sustained GPU/WSL
states and are not used to estimate kernel cost. A same-session fixed-model
ablation below isolates that cost.

Relative to the dev.44 cooldown candidate, final MSE 100% gains
`+0.167202 dB` PSNR and changes SSIM by `-0.004689`. This PSNR gain is the
same magnitude as the frozen 15k common-evaluator gap to LichtFeld, while the
remaining SSIM margin is still large.

## Hot-kernel speed ablation

The dev.44 1.5-million-Gaussian PLY was imported read-only. Both runs used
500 fixed-topology steps, identical views and seed, and ran sequentially in
one service:

| Kernel | Training |
|---|---:|
| Compile-time baseline specialization | 35.749 s |
| Compile-time mixed-MSE specialization | **35.402 s** |

The measured difference is `-0.97%`, within run noise and in favor of the
mixed kernel. The earlier slow prototypes were rejected:

- per-pixel scalar MSE atomics created severe global contention;
- an exact per-step full-frame CUB reduction streamed millions of extra
  samples;
- both were removed from `train_step`.

Exact mixed scalar objectives remain available to evaluation and tests.
Training uses only the fused analytical mixed gradient and reports the
baseline loss.

## Decision

Accept dev.45 as an opt-in convergence mechanism and advance
`photometric_finish=1000`, `photometric_mse_percent=100`, and
`topology_cooldown=1000` to the full Albagnac 15k validation. The candidate
does not add iterations, has no measurable hot-kernel slowdown, and its 4k
PSNR gain is large enough to credibly reach or exceed the frozen LichtFeld
PSNR while retaining the established SSIM margin.

Artifacts:

- 25% quality pilot:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-mse25-cooldown1000-4000/`
- 50% quality pilot:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-noscalar-mse50-cooldown1000-4000/`
- accepted 100% quality pilot:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-specialized-mse100-cooldown1000-4000/`
- same-session kernel baseline:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-kernel-base500/`
- same-session mixed kernel:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-kernel-mse100-500/`
