# Phase 4 persistent geometry Adam result

Date: 2026-07-25
Status: fixed-topology geometry-optimization sub-gate passed; held-out quality
gate open

## Change

`0.5.0-dev.12` connects the position, log-scale, and quaternion gradients
validated in dev.11 to the persistent ordered-alpha trainer.

For each Gaussian, the context now retains:

- 5 projected-center/conic adjoints;
- 10 geometry gradients;
- 20 first/second Adam moments for position, log-scale, and rotation.

That is 35 additional floats, or 136.9 MiB at Albagnac's 1,025,093-Gaussian
cardinality. All buffers remain on device across iterations. The trainer reads
back only its pre-existing pair-count and loss/active-pixel scalars.

The optimizer uses:

| Parameter | Schedule / constraint |
|---|---|
| Position | Initial Gaussian bounding-box diagonal times exponential `1.6e-4` to `1.6e-6` |
| Log-scale | Constant LR `0.005` |
| Rotation | Constant LR `0.001` |
| Log-scale bounds | Initialized global range plus/minus 4 |
| Quaternion | Renormalized after every update |
| Adam | beta1 `0.9`, beta2 `0.999`, epsilon `1e-8` |

Non-finite geometry gradients are ignored. A non-finite position or log-scale
candidate is rejected; a degenerate updated quaternion is reset to identity.

## Correctness gates

- All five native test binaries pass.
- The ordered convergence fixture starts with one anisotropic, rotated
  Gaussian and requires movement in position, scale, and rotation.
- Every downloaded position and log-scale remains finite.
- Every downloaded quaternion has norm within `2e-5` of one.
- Ordered synthetic loss remains decreasing over 30 iterations.
- Public all-component finite differences from dev.11 remain green.

The synthetic ordered fixture changes from `0.346138` to `0.315040` over 30
iterations. It is a wiring and safety test, not a quality benchmark.

## Albagnac 50-step smoke

The smoke used the same 1,376-image, 800x580, 1,025,093-Gaussian dataset and
seed 42 as prior phases.

| Metric | Value |
|---|---:|
| Initial anchor L1 | 0.20055932 |
| Final anchor L1 | 0.16360775 |
| Wall | 6.064 s |
| Trainer compute | 3.989 s |
| Position maximum / mean absolute delta | 0.057201 / 0.015223 |
| Gaussians with changed position | 1,016,554 |
| Gaussians with changed scale | 1,934 |
| Gaussians with changed rotation | 39 |
| Quaternion norm range | 0.99999995 - 1.00000003 |

## Albagnac 500-step result

| Item | Value |
|---|---|
| Dataset | Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Output | `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev12-geometry-500/` |

| Metric | dev.10 fixed geometry | dev.12 geometry Adam |
|---|---:|---:|
| Initial anchor L1 | 0.20055859 | 0.20055868 |
| Final anchor L1 | 0.15530697 | 0.10429499 |
| Anchor reduction | 22.56% | 48.00% |
| End-to-end wall | 30.316 s | 41.851 s |
| Throughput | 16.49 iter/s | 11.95 iter/s |
| Trainer compute | 23.136 s | 39.552 s |
| Foreground image wait | 5.542 s | 0.704 s |
| JPEG decoder service | 25.525 s | 27.040 s |
| Startup | 0.780 s | 0.725 s |
| PLY export | 0.222 s | 0.230 s |
| Sampled total-VRAM delta | not sampled | 838 MiB |
| Peak resident image cache | 267,264,000 B | 267,264,000 B |

Final anchor L1 is 32.85% below the dev.11 fixed-geometry result. Geometry
compute makes trainer compute 70.96% slower than dev.10, while wall is 38.05%
slower. The foreground image wait happened to be much lower in this run, so
compute is the useful comparison.

No CUDA or host OOM occurred. Sampled global GPU memory rose from 872 MiB idle
to 1,710 MiB, a total delta of 838 MiB. The 136.9 MiB geometry-buffer estimate
is consistent with the measured increase over prior persistent runs.

### Geometry movement after 500 steps

| Parameter | Maximum absolute delta | Mean absolute delta | Changed Gaussians |
|---|---:|---:|---:|
| Position | 0.334414 | 0.042127 | 1,025,091 |
| Log-scale | 0.336364 | 0.000106 | 4,890 |
| Rotation | 0.044676 | 0.00000272 | 3,234 |

Final quaternion norms range from `0.99999987` to `1.00000006`. Final
log-scales range from `-2.384575` to `-1.850607`; no configured bound was
reached. 4,890 Gaussians have measurable anisotropy, with a maximum log-scale
axis gap of `0.270125`.

PLY SHA-256:
`bd292cbecc2b5bdd70a861756bbb8b7a5e1ca5e99eab850c55cc1fe60645e6ea`.

The run used the dev.12 working tree based on commit `2a4ebd1`; the final
dev.12 commit contains the same tested optimizer source plus identity and
documentation updates.

## Decision

Accept persistent geometry Adam as the next Phase 4 foundation:

- the million-Gaussian workload fits comfortably in 8 GiB;
- all geometry families move while constraints remain valid;
- anchor reconstruction loss improves materially;
- the measured 71% trainer-compute cost is explicit.

This is not yet a quality-parity result. Anchor training loss can improve while
held-out views regress. No `dronegs-v0.5.0` tag is created until held-out
PSNR/SSIM/LPIPS, DSSIM, progressive SH, and LichtFeld parity are evaluated.
