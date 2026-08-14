# GAJAN component-parallel scalar Adam — dev.55

Date: 2026-08-14

Hardware: NVIDIA RTX 3090, 24 GiB

Dataset: GAJAN R2S, 110 photographs, native 2×2 crops, SH3

Candidate: `codex/gaussian-backward-profile`, based on main `b329f11`

Decision: accepted

## Profiling evidence

Nsight Compute 2022.4 connected to the native process, but the Windows NVIDIA
driver denied hardware performance counters with `ERR_NVGPUCTRPERM`. Nsight
Systems 2024.6.2 captured CUDA APIs under WSL but not kernel records. No driver
security setting was weakened. DroneGS CUDA-event telemetry was therefore
extended to measure objective gradient, gradient reset, raster backward,
geometry backward, scalar Adam, SH Adam and optimizer post-processing while
retaining the existing aggregate fields.

A fixed-topology 100-step run initialized from the retained 191,280-Gaussian
Normal PLY identified scalar Adam as an isolated 0.836 ms cost. Gradient reset
was only 0.099 ms and geometry backward 0.030 ms, so neither was complicated.

## Change under test

Dev.55 assigns one CUDA item to each of the 14 scalar parameters per Gaussian:
three DC, one opacity, three positions, three log-scales and four quaternion
components. A second kernel normalizes each quaternion after all four component
updates. Adam moments, bias correction, learning rates, epsilon values,
non-finite handling and scale clamps remain unchanged.

The original per-Gaussian kernel remains responsible for the six sampled
optimizer-telemetry steps. The component-parallel path handles ordinary
training steps, while telemetry cadence and values retain their existing
contract.

## Fixed-topology result

| Variant | Wall (s) | Train (s) | Scalar Adam (ms) | Optimizer (ms) | Total sampled GPU (ms) |
|---|---:|---:|---:|---:|---:|
| dev.54 detailed control | 2.976 | 0.263 | 0.836 | 1.337 | 5.450 |
| dev.55 candidate | 2.670 | 0.248 | 0.300 | 0.800 | 4.967 |

Scalar Adam improves by `64.2%`, the complete optimizer by `40.1%` and sampled
GPU work by `8.9%`. PSNR changes by `-0.000044` dB and SSIM by `-0.0000004`.

## Full GAJAN results

| Budget/variant | Wall mean (s) | Train mean (s) | Final G mean | Mean PSNR | Pixel PSNR | SSIM |
|---|---:|---:|---:|---:|---:|---:|
| Fast dev.54, 2 runs | 26.554 | 23.140 | 54,895 | 16.8161 | 14.4323 | 0.311226 |
| Fast dev.55, 2 runs | 26.337 | 22.966 | 54,885 | 16.8153 | 14.4425 | 0.311243 |
| Normal dev.54, 2 runs | 76.834 | 73.306 | 191,367 | 18.9683 | 16.4623 | 0.374038 |
| Normal dev.55, 2 runs | 71.779 | 68.203 | 191,548 | 18.9581 | 16.4603 | 0.373970 |

Fast improves `0.8%` wall and training because its final population is only
about 55k. Normal improves `6.6%` wall and `7.0%` training. Normal quality
changes by `-0.0102` dB mean PSNR, `-0.0020` dB pixel-weighted PSNR and
`-0.000068` SSIM; mean population changes by `+0.09%`. These remain inside the
retained run envelope. Observed Normal VRAM stays near 5.0 GiB.

All eight native CPU/CUDA CTests pass on the RTX 3090, including a new test
that forces the component-parallel path and checks finite parameters plus the
unit-quaternion invariant. Logs, manifests, PLYs and profiler outputs are
retained on BIGZEN under:

- `/home/olivier/benchmarks/gajan-profile-dev54-20260814`
- `/home/olivier/benchmarks/gajan-scalar-adam-20260814`
