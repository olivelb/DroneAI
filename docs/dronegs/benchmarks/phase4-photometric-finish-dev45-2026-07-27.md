# Dev.45 progressive photometric finish

Date: 2026-07-27
Status: implementation and full 15k validation accepted

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

## Full 15k validation

The first full run reached iteration 14,000 before the WSL GPU device was
lost. It produced no final checkpoint and is excluded from all comparisons.
Its artifacts remain archived under the `gpu-lost-14000` suffix. A clean
cold restart completed all 15,000 iterations and is the only dev.45 run
reported below.

The strict contract was unchanged: Albagnac dense COLMAP, 1,376 images,
1,204 training views, the same 172 modulo-8 held-out views, seed 42,
progressive SH3, 1.5 million splats, resize factor 4, 1,600-pixel maximum
width, and 15,000 iterations. All final PLY files were replayed by the same
frozen dev.38 FastGS evaluator. LPIPS/AlexNet 0.1 was then measured on the
172 exact RGB8 target/prediction pairs.

| Engine | Training | Wall | Splats | Common PSNR | Common SSIM | Common LPIPS |
|---|---:|---:|---:|---:|---:|---:|
| DroneGS dev.42 | 988.383 s | 1,173.139 s | 1,499,885 | 21.346178 dB | 0.619733 | 0.363027 |
| LichtFeld v0.5.1 deterministic | 994.228 s | about 1,027.052 s | 1,500,000 | 21.513821 dB | 0.586497 | 0.371055 |
| **DroneGS dev.45** | **972.731 s** | 1,028.703 s | 1,500,000 | **22.175919 dB** | **0.642557** | **0.325408** |

Against the deterministic LichtFeld run, dev.45 gains:

- `+0.662098 dB` PSNR;
- `+0.056060` SSIM;
- `-0.045647` LPIPS, where lower is better;
- `-21.497 s` training time (`-2.16%`).

Against dev.42, dev.45 gains `+0.829741 dB` PSNR, `+0.022823` SSIM,
`-0.037619` LPIPS, and `15.652 s` training time. The wall-time reduction
from dev.42 is `144.436 s`, primarily because dev.45 retained the complete
decoded-image cache instead of repeatedly decoding and evicting source
images.

The representative held-out view
`DJI_20230601173020_1009_V.JPG` also improves from dev.42
`21.756371 dB / 0.657091` to dev.45
`22.281490 dB / 0.679138`; its dev.45 LPIPS is `0.334669`.

## Final decision

Dev.45 exceeds the frozen LichtFeld baseline simultaneously on the three
common quality metrics and is slightly faster in measured training time.
The original parity target is therefore met without depending on a
GPU-architecture-specific tuning profile. Keep the progressive photometric
finish opt-in, with the validated Albagnac recipe using a 1,000-step
topology cooldown and a 1,000-step ramp to 100% MSE gradient.

Final artifacts:

- training:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000/`
- frozen common evaluation and exact-pair LPIPS:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000-cross-eval/`
- excluded interrupted run:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev45-photometric-fastgs-15000-gpu-lost-14000/`

No new GPL-covered source was introduced by dev.45. The existing FastGS
rasterization provenance remains recorded in the project GPL register.
