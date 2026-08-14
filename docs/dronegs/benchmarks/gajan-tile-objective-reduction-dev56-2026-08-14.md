# GAJAN tile-local objective reduction — dev.56

Date: 2026-08-14

Hardware: NVIDIA GeForce RTX 3090, 24,576 MiB, driver 591.74

Runtime: CUDA 12.9.2 container

Dataset: GAJAN R2S, 110 photographs, native 2x2 crops, SH3,
`fnv1a64:v3:81814de7903a575f`

Candidate: `codex/gaussian-objective-backward`, based on main `6be6d66`

Decision: accepted

## Change under test

The fused L1/SSIM forward kernel formerly issued one floating-point loss
atomic and one active-pixel count atomic for every active pixel. Dev.56 first
reduces both values in shared memory across each 16x16 CUDA tile and then
issues one pair of global atomics for each non-empty tile.

SSIM moments, valid-window semantics, L1 and DSSIM weights, active-pixel
normalization, backward terms and image gradients are unchanged. The existing
CUDA reference-objective and finite-difference gradient tests remain the
behavioral gate. Floating-point loss accumulation order changes at the final
reduction only.

## Fixed-topology A/B

The exact same 100-step command, view order and retained 191,508-Gaussian
Normal PLY were run with the merged dev.55 control and the dev.56 candidate.
Topology refinement and the photometric finish were disabled.

| Variant | Training (s) | Mean sampled objective (ms) | Final PSNR | Final SSIM |
|---|---:|---:|---:|---:|
| dev.55 control | 0.4616 | 1.1621 | 18.870893 | 0.37432370 |
| dev.56 candidate | 0.3813 | 0.3156 | 18.870901 | 0.37432393 |

The objective stage improves by `72.8%` and the isolated training interval by
`17.4%`. Final PSNR changes by `+0.000008` dB and SSIM by `+0.00000024`.

## Full GAJAN results

Each accepted budget was repeated twice. Dev.55 values are the retained
two-run controls from the preceding qualification.

| Budget/variant | Wall mean (s) | Train mean (s) | Final G mean | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Fast dev.55 | 26.337 | 22.966 | 54,885 | 16.8153 | 14.4425 | 0.311243 |
| Fast dev.56 | 20.896 | 17.337 | 54,889 | 16.8292 | 14.4478 | 0.311358 |
| Normal dev.55 | 71.779 | 68.203 | 191,548 | 18.9581 | 16.4603 | 0.373970 |
| Normal dev.56 | 58.948 | 55.307 | 191,547 | 18.9800 | 16.4772 | 0.374084 |

Fast improves `20.7%` wall and `24.5%` training. Normal improves `17.9%`
wall and `18.9%` training. Relative to dev.52, the cumulative Normal
improvement is `30.2%` wall and `31.7%` training. Quality changes remain
inside retained run variation and the Normal population is effectively
unchanged. No additional persistent GPU allocation is introduced.

The candidate benchmark binary still reported the dev.55 version string
because version metadata was advanced only after the A/B decision. The source
change and benchmark branch are recorded above; the final dev.56 build and
CTest pass use the advanced version metadata.

## Validation and retained evidence

All eight native CPU/CUDA CTests pass on the RTX 3090, including a new 29x27
partial-edge-tile objective/reference regression. Logs, manifests, PLYs,
evaluation images and the failed preflight container are retained on BIGZEN
under:

- `/home/olivier/benchmarks/gajan-objective-reduction-dev56-20260814`
- Docker containers named `dronegs-dev56-*`

The next measured optimization target is the structural raster backward pass,
which reaches about 1.21 ms late in the retained Normal run. It is deferred to
a separate phase because changing its atomic accumulation strategy carries a
higher numerical and correctness risk than the forward reduction.
