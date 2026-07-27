# Dev.46 Savères checkpoint/canary validation

Date: 2026-07-27
Status: accepted for the production pipeline

## Scope

This run validates the complete DroneGS-only production path on the second
large Mavic 3E RTK scene:

- native periodic checkpoints;
- atomic interruption/restart state;
- automatic pipeline resume;
- post-training PSNR/SSIM canary;
- exact-pair external LPIPS;
- representative preview and progress-page data;
- read-only COLMAP input.

No LichtFeld executable, image, checkout, adapter, or vcpkg tree participates
in this run. Historical LichtFeld numbers remain documentation-only
references.

## Dataset and contract

- COLMAP workspace:
  `/home/olivier/droneAI-workspaces/saveres-mavic3e-full/dense`
- images: 1,065
- train/held-out split: 931 / 134, deterministic modulo 8
- initial sparse Gaussians: 642,161
- iterations: 15,000
- seed: 42
- maximum population: 1,500,000
- resize factor / maximum width: 4 / 1,600 pixels
- progressive SH: degree 0 to 3, one activation every 1,000 steps
- topology cooldown: final 1,000 steps
- photometric finish: final 1,000 steps to 100% active-pixel MSE gradient
- checkpoint interval: 2,000 steps
- canary thresholds: PSNR >= 18.0 dB and SSIM >= 0.35

The runtime used the numerically equivalent pre-rename aliases for the
reference optimizer and spatial-bound pruning profiles. Dev.46 source and
new manifests expose only the neutral names `reference-absolute`,
`spatial-bounds`, and `bounded`.

## Result

| Metric | Savères dev.46 |
|---|---:|
| Completed iterations | 15,000 / 15,000 |
| Final Gaussians | 1,500,000 |
| Training time | 2,455.774 s (40.93 min) |
| Wall time | 2,507.560 s (41.79 min) |
| Checkpoint overhead | 1.180 s |
| Held-out evaluation time | 9.966 s |
| PSNR | 19.163038 dB |
| SSIM | 0.456047 |
| LPIPS AlexNet 0.1 | 0.551232 |
| LPIPS views | 134 |
| Final loss | 0.168005 |

The canary passed both gates. The exact-pair LPIPS distribution has median
`0.551411`, p95 `0.707836`, and maximum `0.996398`.

The median-LPIPS representative view is
`DJI_20240930115834_0537_V.JPG` (`000067.ppm`, LPIPS `0.551385`).
It is geometrically coherent, but visibly softer than the source target.
That limitation is recorded rather than hidden: the scene passes the
production safety floor, while future scene-specific quality work should
target fine texture and foliage.

## Checkpoint/restart canary

A separate Gajan integration canary compared:

1. 200 uninterrupted steps;
2. 100 steps, deliberate process exit 75 with an atomic checkpoint, then
   resume to step 200.

Both ended with 9,324 Gaussians. The resumed result differed from the
continuous result by only `-0.000126 dB` PSNR and `+0.000017` SSIM. Native
unit coverage additionally compares every restored parameter family and
optimizer moment with a `2e-6` tolerance. The checkpoint includes dataset
and configuration fingerprints, iteration/topology counters, progressive-SH
state, all Gaussian attributes, all Adam moments, refinement statistics, and
the original initial-quality measurements.

The Savères checkpoint reached about 1.1 GiB at the 1.5-million cap. It is
kept on an interrupted or failed run, and deleted only after the final PLY,
manifest, exact-pair metrics, and canary have all been published
successfully. The durable successful result is therefore about 340 MiB,
including a 338 MiB PLY and the preview/evaluation files.

## Validation matrix

- release CUDA native build: passed;
- six of six C++/CUDA/LPIPS suites: passed;
- pipeline/backend Python tests: 43 passed;
- checkpoint format v2 reads v1 state and preserves initial metrics in v2;
- portable CUDA image: Turing through Blackwell cubins;
- GPU startup on the local NVIDIA device: passed;
- image provenance/source/notices audit: passed;
- forbidden LichtFeld/vcpkg runtime artifacts in the image: none.

Artifacts:

- training, evaluation, canary and preview:
  `/home/olivier/droneAI-workspaces/saveres-dronegs-dev46-checkpoint-canary-15000/`
- log:
  `/home/olivier/droneAI-workspaces/saveres-dronegs-dev46-checkpoint-canary-15000.log`
- checkpoint/restart integration canary:
  `/home/olivier/droneAI-workspaces/gajan-dronegs-dev46-checkpoint-resumed-200-v3/`

The source dataset was mounted read-only throughout.
