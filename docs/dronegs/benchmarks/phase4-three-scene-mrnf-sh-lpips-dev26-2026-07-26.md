# Phase 4 three-scene MRNF, progressive SH, and LPIPS validation

Date: 2026-07-26

Version under test: `0.5.0-dev.25` (`47319da90ffa1cffd32b1f8d738c3d4d707d435a`)

Documentation and evaluator hardening: `0.5.0-dev.26`

## Decision

Accept dev.25 as the first bounded real-scene execution proof for the complete
MRNF lifecycle, progressive degree-3 spherical harmonics, and exact-pair
external LPIPS. Keep the native trainer opt-in and keep the Phase 4 production
gate open.

All three runs completed, reached SH degree 3, exercised MRNF refinement, wrote
valid degree-3 PLY output, and completed LPIPS without rerunning COLMAP. The two
large scenes stayed well within the RTX 4070 Laptop GPU's 8 GiB capacity during
the observed training window. This is not a converged quality comparison: the
large scenes received only 220 iterations and one topology refinement.

## Inputs

Existing COLMAP outputs were mounted read-only. No source photograph, feature
database, match graph, sparse model, bundle adjustment, or undistortion output
was modified.

| Scene | COLMAP images | Initial points/Gaussians | Dataset fingerprint | Run |
|---|---:|---:|---|---:|
| GAJAN smoke | 25 | 9,324 | `fnv1a64:23fa4f3be11d1670` | 1,200 iterations |
| Savères Mavic 3E RTK | 1,065 | 642,161 | `fnv1a64:65e7f5ec5e4d53f8` | 220 iterations |
| Albagnac Mavic 3E RTK | 1,376 | 1,025,093 | `fnv1a64:b52de467fbfc898e` | 220 iterations |

Common large-scene settings were resize factor 4, maximum width 1,600,
`calibrated-dc-0.020-opacity`, progressive SH degree 3 with a 50-iteration
activation interval, test stride 64, and exact RGB8 held-out pair export.
GAJAN used a 200-iteration SH activation interval and test stride 5.

## Results

| Scene | Held-out views | Initial PSNR | Final PSNR | Initial SSIM | Final SSIM | LPIPS mean |
|---|---:|---:|---:|---:|---:|---:|
| GAJAN smoke | 5 | 9.33557 | 14.13256 | 0.077502 | 0.205984 | 1.046475 |
| Savères | 17 | 14.55384 | 15.72116 | 0.099633 | 0.110614 | 1.201670 |
| Albagnac | 22 | 14.21330 | 16.86978 | 0.187146 | 0.240971 | 1.085614 |

LPIPS uses the official package, AlexNet backbone, version 0.1, exact final
trainer RGB8 PPM pairs, and `[-1, 1]` input normalization. Lower is better, but
these absolute values must not be interpreted as parity evidence without the
same-view LichtFeld controls.

| Scene | LPIPS median | LPIPS p95 | LPIPS maximum |
|---|---:|---:|---:|
| GAJAN smoke | 1.038515 | 1.200990 | 1.226267 |
| Savères | 1.206320 | 1.234316 | 1.243876 |
| Albagnac | 1.071081 | 1.297270 | 1.422713 |

## MRNF lifecycle and scaling

| Scene | Refinements | Added | Pruned | Reused | Final Gaussians | Wall time |
|---|---:|---:|---:|---:|---:|---:|
| GAJAN smoke | 6 | 4,669 | 3 | 3 | 13,990 | 16.79 s |
| Savères | 1 | 44,950 | 1 | 1 | 687,110 | 62.49 s |
| Albagnac | 1 | 71,743 | 7 | 7 | 1,096,829 | 83.94 s |

Training time was 14.13 s, 53.12 s, and 75.46 s respectively. Final PLY sizes
were 3,303,207 bytes, 162,159,528 bytes, and 258,853,213 bytes. Albagnac
completed the 220-step run without OOM at the million-Gaussian scale; observed
GPU allocation before refinement was approximately 2.9 GiB.

Prune counts are intentionally small at this early budget because initial
COLMAP opacities and scales are still healthy. The important result is that
prune, dense compaction, Adam-moment preservation, slot reuse, split append,
means noise, and decay execute on both 642k and 1.0M starting topologies.

## CUDA architecture audit

`cuobjdump` shows that the binary used by these runs contains an sm_52 cubin;
the accompanying compute_52 PTX is JIT-compiled by the current NVIDIA driver
for the RTX 4070. The build cache therefore did not honor the project's intended
native sm_89 default. The measured runs remain valid for that exact binary, but
their timings are not native-Ada code-generation evidence.

A clean CUDA 12.8 build was attempted for both sm_86 and sm_89. Both fail at
device link in CUB `DeviceRadixSort` Policy900: its one-sweep and downsweep
kernels require `0xca00` and `0xc600` bytes of static shared memory while
`nvlink` permits `0xc000` bytes. Rebuilding dev.26 with the exact validated
sm_52 target passes all six CPU/CUDA/LPIPS-tool suites.

The preferred repair is to radix-sort compact 32-bit projected-record indices
instead of the full projected-record value, then gather records in sorted
order. This reduces CUB's per-block shared payload and should permit a native
sm_89 image without changing depth/tile ordering. It requires its own
correctness and throughput phase; this report does not silently change the
validated renderer.

## LPIPS output hardening discovered by the run

The dev.23 atomic writer created temporary files with mode `0600`. Because the
evaluator container runs as root, all three completed manifests ended as
root-owned `0600` files and were unreadable to the workspace user. On a
non-root-owned input, replacement could also change the owner.

Dev.26 preserves the owner and mode of existing atomic targets. For new
`lpips.csv` and `lpips.json` files it uses the evaluation directory owner and
mode `0644`. A dependency-free unit test covers both existing-file mode
preservation and new-file mode/owner behavior. The README invocation is also
corrected to pass `--evaluation-dir` and `--manifest` explicitly and to persist
the model cache in a named Docker volume.

## License boundary

The LPIPS evaluator, tests, documentation, manifest handling, and native host
orchestration remain MIT where already marked. The MRNF-derived CUDA lifecycle,
selection, optimizer, and rasterization behavior identified in
`docs/dronegs/GPL_COMPONENTS.md` remains GPL-3.0-or-later. This validation does
not change that boundary; binaries linked with those CUDA translation units
remain GPL-covered.

## Remaining gates

1. Add checkpoint/resume before attempting long runs on 1,000+ images.
2. Run sufficiently long, same-view DroneGS and pinned LichtFeld controls on
   GAJAN and at least one large scene; compare PSNR, SSIM, LPIPS, time, and
   peak VRAM.
3. Profile and eliminate the current host-mediated full-state topology
   compaction path before claiming speed parity on many refinements.
4. Replace the large-value CUB radix-sort payload, link native sm_89, and
   compare correctness plus throughput against this exact validated binary.
5. Validate image quality visually and downstream, including orthomosaic
   coverage and labelled-task non-regression.
6. Defer the combined approximately 2,000-photo Albagnac throughput test until
   COLMAP bundle-adjustment strategy is GPU-accelerated or otherwise bounded,
   as requested for this phase.
