# Phase 4 — FastGS compatibility and same-split parity (dev38)

Date: 2026-07-26  
Version: `0.5.0-dev.38`  
Disposition: accepted as the opt-in quality profile

## Question and method

The previously quoted Albagnac gap compared DroneGS's 172 held-out images
against a LichtFeld report whose 172 images came from a different scene
ordering. Dev.38 first separates renderer behavior from learned parameters:

1. import the exact LichtFeld Gaussian PLY;
2. render it with DroneGS on DroneGS's fixed cameras and held-out split;
3. make covariance, FOV clamp, support, alpha ceiling, and backward one gated
   FastGS-compatible contract;
4. retrain from the existing dense COLMAP product without rerunning COLMAP.

The direct PLY import supports arbitrary float-property order, ignores
unneeded float properties such as normals, validates all required 3DGS
fields, and preserves SH degree 0–3.

## Causal result

| Exact model and evaluator | PSNR | SSIM |
|---|---:|---:|
| LichtFeld PLY, historical DroneGS renderer | 15.221908 | 0.356678 |
| LichtFeld PLY, dev.38 FastGS-compatible renderer | 18.900364 | 0.428674 |
| DroneGS dev.38, 1,000 steps | 18.963013 | 0.428099 |
| DroneGS dev.38, 1,200 steps | **19.157091** | **0.440746** |

The coherent renderer contract recovers `+3.678456 dB` on the exact same
LichtFeld parameters. The accepted 1,200-step model exceeds that same-split
oracle by `+0.256727 dB` and `+0.012072 SSIM`.

LichtFeld's own `21.068552 / 0.631048` report is valid for its own held-out
ordering. Recomputing metrics from all 172 LichtFeld composites gives
`21.067955 / 0.630510`, confirming that both implementations use compatible
PSNR/SSIM formulae. It is not a same-image-set number and therefore is not
used as the parity gate.

## Perceptual and robustness checks

| Scene/profile | Steps | PSNR | SSIM | LPIPS |
|---|---:|---:|---:|---:|
| Albagnac dev.36 | 500 | 18.282480 | 0.371775 | 0.678306 |
| Albagnac dev.38 | 1,000 | 18.963013 | 0.428099 | **0.625404** |
| GAJAN dev.36 | 1,200 | 14.507236 | 0.286847 | — |
| GAJAN dev.38 | 1,200 | **16.244888** | **0.308946** | — |
| Savères dev.36 | 1,000 | 17.765158 | 0.337950 | — |
| Savères dev.38 | 1,000 | **17.795483** | **0.340590** | — |

Albagnac exact-pair AlexNet LPIPS improves by `0.052903` at 1,000 steps.
GAJAN gains `+1.737653 dB / +0.022099 SSIM`; Savères gains
`+0.030325 dB / +0.002640`. These controls use the existing GAJAN, Savères,
and Albagnac COLMAP products. No bundle adjustment or combined approximately
2,000-photo Albagnac throughput run was performed.

## Timing

| Run | Training | Evaluation | Wall | Final Gaussians |
|---|---:|---:|---:|---:|
| Albagnac dev.38, 1,000 | 29.109 s | 19.894 s | 95.868 s | 1,380,700 |
| Albagnac dev.38, 1,200 | 32.149 s | 20.211 s | 114.897 s | 1,445,545 |
| GAJAN dev.38, 1,200 | 10.000 s | 0.222 s | 11.391 s | 13,871 |
| Savères dev.38, 1,000 | 21.008 s | 19.999 s | 108.139 s | 875,705 |

Quality parity is established; this report does not claim equal
quality-to-time because the pinned LichtFeld run used 500 optimizer steps and
a different validation ordering.

## Implementation and provenance

The successful profile is architecture-independent. Local builds use CMake
`native`; portable builds contain recent-NVIDIA cubins and let the driver pick
the target at runtime.

The FastGS equations were adapted from pinned LichtFeld revision
`1004c0841a3776e3f67866ff34101fbc9677397f` into
`app1-colmap/dronegs/cuda/rasterization.cu`. That translation unit and the
resulting linked native binary are GPL-3.0-or-later. The PLY reader and CLI
orchestration are original MIT code.

## Candidate audit

- `nerfstudio-project/gsplat`: its AbsGrad and compensated anti-alias
  mechanisms informed dev.36/dev.37. Its newer AccuTile intersection and
  native MCMC perturbation are promising throughput candidates, but neither
  explains the quality gap closed here.
- `TY424/AbsGS`: confirms the value of homodirectional projected-mean
  gradients already integrated in dev.36.
- `ubc-vision/3dgs-mcmc`: relocation and stochastic-noise topology remain a
  credible post-parity quality/convergence experiment, but would change the
  optimizer lifecycle substantially.
- `pointrix-project/pointrix` and `GAP-LAB-CUHK-SZ/gaustudio`: useful modular
  experiment frameworks and implementation indexes; no source is imported
  because their framework layers do not address the measured renderer
  incompatibility directly.

The dev.38 finding is therefore deliberately narrower: fix the observed
renderer contract first, retain MCMC relocation and tighter tile intersection
as separately measurable follow-up experiments.

## Artifacts

- LichtFeld oracle PLY:
  `/home/olivier/droneAI-workspaces/albagnac-lichtfeld-dev13-heldout-500/splat_500.ply`
- cross-evaluation:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-lichtfeld-ply-cross-eval`
- accepted Albagnac run:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-1200`
- exact-pair LPIPS run:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev38-fastgs-1000`
- GAJAN robustness run:
  `/home/olivier/droneAI-workspaces/gajan-dronegs-dev38-fastgs-r4-1200`
- Savères robustness run:
  `/home/olivier/droneAI-workspaces/saveres-dronegs-dev38-fastgs-1000`
