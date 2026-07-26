# DroneGS GPL and third-party provenance register

Policy: GPL components are allowed. Their provenance and redistribution
obligations remain explicit at file, binary, container, and documentation
level. This is an engineering inventory, not legal advice.

## Current components

| Component | Revision/source | License | Local use | Treatment |
|---|---|---|---|---|
| LichtFeld-Studio | `MrNeRF/LichtFeld-Studio` at `1004c0841a3776e3f67866ff34101fbc9677397f` | GPL-3.0-or-later | Ignored external source and trainer binary | Keep license, exact source, notices, build scripts, and modification record available |
| LichtFeld minimal patches | `app1-colmap/patches/lichtfeld-*.patch` | Modifications to GPL work | Headless/minimal build changes | Treat patches and resulting binary as GPL-covered modifications |
| LichtFeld runtime image | `Dockerfile.lichtfeld` and LichtFeld stage in `Dockerfile.base` | Contains GPL binary | Subprocess runtime | Bundle notices and ensure corresponding source is obtainable |
| LichtFeld Python adapters | `gaussian_ortho/lichtfeld_trainer.py`, `gaussian_training/backends.py` | Original DroneAI code; process boundary | CLI adapters and PLY consumer | Keep process boundary; do not copy GPL implementation into wrappers |
| DroneGS native trainer through dev.14 | `app1-colmap/dronegs/`, excluding the dev.15 GPL units below | MIT; original DroneAI code | Anisotropic geometry-optimized ordered-alpha CUDA foundation | JPEG/scaled-IDCT orchestration, bounded decode queue/cache, covariance/conics, analytical geometry and DSSIM backward, persistent Adam/schedules, held-out split, CUDA PSNR/SSIM, rasterizer, and training context were independently implemented; dev.13/dev.14 record the GPL control separately |
| DroneGS MRNF growth, selection, edge, optimizer, progressive-SH, and topology-lifecycle adaptation | LichtFeld `src/training/strategies/mrnf.cpp`, `src/training/strategies/strategy_utils.cpp`, `src/training/optimizer/adam_optimizer.cpp`, `src/training/rasterization/fastgs/optimizer/src/adam_api.cu`, `src/training/kernels/mrnf_kernels.cu`, `src/training/kernels/densification_kernels.cu`, `src/training/rasterization/edge_rasterizer.cpp`, `src/training/rasterization/edge_compute/rasterization/include/kernels_forward.cuh`, `src/training/rasterization/fastgs/rasterization/include/kernels_forward.cuh`, `src/training/rasterization/fastgs/rasterization/include/kernels_backward.cuh`, and `src/training/trainer.cpp` at `1004c0841a3776e3f67866ff34101fbc9677397f`; local `app1-colmap/dronegs/cuda/rasterization.cu` and `app1-colmap/dronegs/cuda/trainer.cu`, modified 2026-07-25 through 2026-07-26 | GPL-3.0-or-later | Dev.15 error-map weighting, cadence, capacity growth, and split; dev.16 weighted-Gumbel and edge guidance; dev.17 optimizer rates, epsilon, bounds normalization, and schedules; dev.18 dual profiles and effective-update telemetry; dev.19 per-family epsilon and exact one-family ablations; dev.20 strict DC-plus-opacity combination; dev.21 intermediate-DC-plus-opacity calibration profiles; dev.22 two-scene validation; dev.24 progressive SH cadence and optimizer integration; dev.25 prune/reuse, deterministic means noise, opacity/scale decay, and hard compaction preserving optimizer state; dev.27 compact projected-record sorting, dev.28 historical Ada radix/occupancy tuning, dev.29 shared-memory backward batching, dev.30 removal of architecture-specific dispatch, and dev.32 `[0,4]` live SH color inside the same GPL translation unit | Both combined CUDA translation units carry GPL SPDX/copyright headers; distribute the resulting native binary as GPL-covered, retain this modification record, and make corresponding source available |
| DroneGS MRNF local-scale initialization | LichtFeld `src/core/splat_data.cpp::compute_mrnf_knn_log_scales` at `1004c0841a3776e3f67866ff34101fbc9677397f`; local `app1-colmap/dronegs/src/model.cpp`, modified 2026-07-26 | GPL-3.0-or-later | Dev.31 central-75% extent bound and two-nearest-neighbour isotropic log-scale formula | The local translation unit carries a GPL SPDX/copyright header and source-revision notice; its balanced KD tree and parallel exact-query implementation are original DroneAI code, but the combined file and resulting native binary are distributed as GPL-covered |
| DroneGS CPU SH color compatibility oracle | LichtFeld `src/training/rasterization/fastgs/rasterization/include/rasterization_config.h`, `kernels_forward.cuh`, and `kernels_backward.cuh` at `1004c0841a3776e3f67866ff34101fbc9677397f`; local `app1-colmap/dronegs/src/rasterization.cpp`, modified 2026-07-26 | GPL-3.0-or-later | Dev.32 `[0,4]` splat-color ceiling and matching DC/SH live-gradient interval | The CPU oracle translation unit carries a GPL SPDX/source notice; distribute it and the linked native binary consistently with the GPL |
| PerceptualSimilarity / LPIPS | `richzhang/PerceptualSimilarity`, PyPI package `lpips==0.1.4` | BSD-2-Clause | Separate final held-out evaluator in `tools/lpips_eval.py` and `Dockerfile.lpips` | Keep the evaluator outside the native trainer, pin the package, retain its notice, and record the separately downloaded AlexNet weights |
| PyTorch / torchvision | `torch==2.7.1`, `torchvision==0.22.1`, CPU wheels | BSD-style | Runtime for the separate LPIPS evaluator | Pin versions and retain upstream notices; these dependencies are not linked into the DroneGS CUDA binary |
| gsplat | Official `nerfstudio-project/gsplat`; revision not selected | Apache-2.0 | Candidate future rasterizer source | Import from official source, pin revision, retain notices, list adapted files |
| INRIA 3DGS code | Reference implementation; revision not selected | Custom research license | Algorithmic reference only | Do not copy source until separately approved |
| ImprovedGS+ | Revision and license not audited | To be determined | Post-parity experiment candidate | No source import until audited |
| NVIDIA CUB | 2.7.0 from CUDA Toolkit 12.8.1 | BSD-3-Clause | Header-only radix sort and scan in the native raster pipeline | Retain NVIDIA copyright and BSD notice with binary distributions |
| NVIDIA CUDA/images | NVIDIA distributions | NVIDIA terms | Build/runtime | Preserve redistribution notices and terms |
| COLMAP | Pinned by DroneAI | BSD-3-Clause | Reconstruction/input format | Preserve license and attribution |

## File-level rules

Source copied or adapted from a GPL project must:

1. carry the correct SPDX identifier;
2. identify the upstream project and exact revision;
3. state that it was modified and give the date;
4. be listed here with its local path;
5. remain in a component distributed consistently with the GPL;
6. include the material needed to reproduce the binary.

Original DroneGS files implementing published algorithms without copying source
carry the selected project SPDX identifier and must not claim an inapplicable
upstream copyright.

## Release checklist

- [ ] Exact source revisions are pinned.
- [ ] Licenses and third-party notices ship with each relevant image.
- [ ] Modified GPL files and patches are identified.
- [ ] Corresponding source matches shipped object code.
- [ ] Apache/BSD/MIT notices are retained.
- [ ] INRIA's custom license is reviewed for reused source or assets.
- [ ] Model weights and datasets have separate provenance.
- [ ] README does not describe LichtFeld as BSD-3-Clause.
