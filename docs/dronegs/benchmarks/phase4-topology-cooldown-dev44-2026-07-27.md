# Dev.44 fixed-topology convergence cooldown

Date: 2026-07-27
Status: opt-in implementation accepted; no full 15k rerun yet

## Question

Can DroneGS recover final photometric quality and reduce topology overhead by
reserving the end of the existing iteration budget for fixed-topology
optimizer convergence?

Dev.44 adds `--topology-cooldown N`. Refinement remains unchanged through
`iterations - N`; the final `N` steps keep the Gaussian topology fixed.
The default is zero and therefore preserves dev.43 exactly.

## Validation

- Release CUDA portable build completed.
- Six of six C++/CUDA/Python suites pass.
- Albagnac dense COLMAP data remained mounted read-only.
- All pilots used one binary, seed 42, 4,000 steps, progressive SH3,
  resize factor 4, 1,600 maximum width, the 1.5 million cap, the strict
  modulo-8 split, LichtFeld-absolute rates, LichtFeld bounds, structural
  FastGS, and the dev.43 bounded resident cache.

## Result

| Metric | Cooldown 0 | Cooldown 1,000 | Cooldown 2,000 |
|---|---:|---:|---:|
| Training | 178.106 s | 173.554 s | **166.440 s** |
| Wall | 242.061 s | 237.494 s | **231.999 s** |
| Final loss | 0.171474 | 0.170790 | **0.170074** |
| Held-out PSNR | 21.274694 dB | **21.322853 dB** | 21.315660 dB |
| Held-out SSIM | **0.596830** | 0.596799 | 0.596636 |
| Topology refinements | 20 | 15 | 10 |
| Cache misses / evictions | 1,376 / 0 | 1,376 / 0 | 1,376 / 0 |
| Peak decoded RGB8 RAM | 1,915,392,000 B | 1,915,392,000 B | 1,915,392,000 B |

Relative to the control:

- cooldown 1,000: `+0.048159 dB` PSNR, `-0.000031` SSIM,
  `-0.000684` final loss, `-2.6%` training and `-1.9%` wall;
- cooldown 2,000: `+0.040966 dB` PSNR, `-0.000194` SSIM,
  `-0.001400` final loss, `-6.5%` training and `-4.2%` wall.

## Decision

Keep the mechanism opt-in and select 1,000 steps as the quality-oriented
candidate. It has the best PSNR and an immaterial SSIM delta while also
reducing time.

Do not launch another 15k for this change alone. The measured `+0.048 dB`
directional gain is useful but does not by itself credibly close the remaining
`0.168 dB` gap to the frozen LichtFeld reference. Combine it with a stronger,
independently validated photometric-convergence change before paying for the
next full run.

Artifacts:

- control:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev44-cooldown0-4000/`
- cooldown 1,000:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev44-cooldown1000-4000/`
- cooldown 2,000:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev44-cooldown2000-4000/`
