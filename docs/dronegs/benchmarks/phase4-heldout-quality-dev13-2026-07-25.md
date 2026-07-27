# Phase 4 held-out quality result

Date: 2026-07-25
Status: held-out measurement gate passed; quality parity failed

## Protocol

DroneGS `0.5.0-dev.13` adds an opt-in validation split matching the pinned
LichtFeld implementation:

```text
held out when scene_index % test_every == 0
```

With `test_every=8`, the Albagnac scene contains 1,204 training views and 172
held-out views. Held-out indices never enter the shuffled Adam schedule. Both
backends read `images.bin` sequentially and therefore apply the rule to the
same camera order.

The native evaluator computes:

- PSNR over every RGB sample in `[0,1]`, with MSE floored at `1e-10`;
- SSIM per RGB channel using an 11x11 Gaussian window, sigma 1.5, constants
  `C1=0.01^2` and `C2=0.03^2`, and valid-padding mean;
- arithmetic means across held-out cameras, matching LichtFeld.

DroneGS computes metric samples and reductions on CUDA. A direct CPU oracle
checks both metrics in the native test suite. Only scalar results return to the
host unless lossless prediction export is requested.

## DroneGS result

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Train / held-out | 1,204 / 172 |
| Resolution | 800 x 580 |
| Initial/final Gaussians | 1,025,093 / 1,025,093 |
| Iterations / seed | 500 / 42 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev13-heldout-500/` |

| Metric | Initial | Final | Change |
|---|---:|---:|---:|
| Mean PSNR | 14.0631 dB | 17.1212 dB | +3.0580 dB |
| Median PSNR | 14.1367 dB | 17.1264 dB | +2.9897 dB |
| Mean SSIM | 0.181077 | 0.241900 | +0.060824 |
| Median SSIM | 0.170659 | 0.217396 | +0.046737 |
| Mean active-pixel coverage | 0.996239 | 0.999862 | +0.003623 |

PSNR improves on 171 of 172 held-out views; SSIM improves on all 172.

| Timing / artifact | Value |
|---|---:|
| Trainer compute | 39.310 s |
| Initial + final evaluation | 18.812 s |
| End-to-end wall | 61.109 s |
| JPEG service | 46.578 s |
| Foreground image wait | 11.978 s |
| Final prediction PPM files | 172 |
| Prediction bytes | 239,426,580 |
| Final PLY SHA-256 | `08a5f808796429c0aab84a0e173f778075c0ead6ada69dfa740476107dafbdea` |

The run completed without CUDA or host OOM.

## Pinned LichtFeld control

The GPL LichtFeld runtime image is unchanged:

```text
sha256:71913f535a208879b9cd2e84f17895849c51de53e457149bd12c85c95e44568f
```

The control uses the same dataset, resize, black background, 500 iterations,
MRNF strategy, SH degree 0, 1.5-million cap, tile mode 4, and `test_every=8`.
The exact optimization config was historically stored at
`benchmarks/configs/lichtfeld-albagnac-heldout-500-dev13.json`; dev.46 removes
that executable launch configuration with the retired runtime. The immutable
parameters and hashes in this report remain the audit record.

| Metric | DroneGS | Pinned LichtFeld | DroneGS gap |
|---|---:|---:|---:|
| Held-out PSNR | 17.1212 dB | 21.0686 dB | -3.9474 dB |
| Held-out SSIM | 0.241900 | 0.631048 | -0.389148 |
| Final Gaussians | 1,025,093 | 1,173,540 | -148,447 |
| Reported trainer/application time | 39.310 s compute | 51.962 s | not directly comparable |
| Full measured command wall | 61.109 s direct | 80.26 s Docker | not directly comparable |

The quality comparison is valid; the timing columns are diagnostic only.
DroneGS evaluates twice and runs directly under WSL, while the LichtFeld wall
includes container startup, CUDA JIT, one final evaluation, PNG output, and a
209 MB resume checkpoint.

## Interpretation

The gate now distinguishes convergence from parity. Fixed-topology L1 training
does generalize to unseen images, but remains substantially below the
LichtFeld control.

This control does not isolate one cause. LichtFeld already has:

- a mixed L1/DSSIM objective with weight 0.2;
- MRNF growth, reaching 148,447 more Gaussians by step 500;
- its mature parameter schedules and rasterization details.

The next controlled implementation should add DSSIM while retaining fixed
topology, then rerun this exact split. Progressive SH follows. MRNF topology is
deferred until the photometric and appearance gaps are measured independently.

LPIPS remains `null`. The lossless final predictions exist, but the machine has
no local PyTorch, torchvision, Pillow, NumPy, LPIPS package, or pinned
perceptual-model weights. No dependency or model was downloaded during this
phase.

## Decision

Accept dev.13 as the reproducible held-out measurement foundation. Do not tag
`dronegs-v0.5.0`: PSNR and SSIM fail the provisional parity thresholds by a
large margin. Proceed with DSSIM as dev.14, using this run and the pinned
LichtFeld control as the fixed oracle.
