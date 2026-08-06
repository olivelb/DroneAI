# CUDA 12.9.2 runtime qualification — 2026-08-06

## Purpose and scope

This record closes the audit gap between the CUDA 12.9.2 image contracts and
their execution on physical NVIDIA hardware. It validates the native DroneGS
test suites and NVIDIA driver injection in both production runtime bases. It is
not a dataset-quality benchmark, a complete DroneAI deployment test or an
immutable release attestation.

The run used the audit-hardening working tree based on Git commit
`c89f31e1cee2d7ea0e2f876d09a40b9f31a5c1d7`. The working tree contained pending
changes, including the qualification reporting and COLMAP container hardening.
Consequently, this document is local pre-commit evidence; the self-hosted GPU
workflow must be rerun after commit to produce release evidence.

## Environment

| Item | Observed value |
|---|---|
| Host environment | Ubuntu under WSL2 |
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Driver reported in containers | 610.62 |
| GPU compute capability | 8.9 |
| Docker server | 29.3.0 |
| CUDA container version | 12.9.2 |
| CUDA compiler | NVIDIA 12.9.86 |
| GNU C++ compiler | 13.3.0 |
| Python | 3.12.3 |

The shared qualification command was:

```bash
set -o pipefail
scripts/ci/validate_cuda_containers.sh gpu 2>&1 | tee gpu-validation.log
```

It exercised these image contracts:

- `nvidia/cuda:12.9.2-devel-ubuntu24.04`
- `nvidia/cuda:12.9.2-runtime-ubuntu24.04`
- `nvidia/cuda:12.9.2-cudnn-runtime-ubuntu24.04`

## Native build and test result

The development container completed all 28 requested build actions, including
the DroneGS executable and its core, rasterization, CUDA and training test
binaries.

| CTest suite | Result | Time |
|---|---:|---:|
| `dronegs_core_tests` | Passed | 0.00 s |
| `dronegs_rasterization_tests` | Passed | 0.00 s |
| `dronegs_cuda_tests` | Passed | 0.22 s |
| `dronegs_rasterization_cuda_tests` | Passed | 0.16 s |
| `dronegs_training_tests` | Passed | 0.22 s |
| `dronegs_lpips_tool_tests` | Passed | 0.15 s |

CTest reported 100% success: 6 tests passed, 0 failed, with 0.76 seconds of
test execution time. The complete container build, native execution and
runtime-smoke command completed in approximately 217 seconds.

## Production runtime driver injection

Docker successfully exposed the RTX 4070 and driver 610.62 inside both runtime
images:

| Runtime image | Pulled digest | Result |
|---|---|---|
| `nvidia/cuda:12.9.2-cudnn-runtime-ubuntu24.04` | `sha256:070f8f2672df1b05b84c0409a5fd1d54ddfd646e5b9d8dee7878131271b563fc` | Passed |
| `nvidia/cuda:12.9.2-runtime-ubuntu24.04` | `sha256:6d2a0dabc50c3bf14d27fc66822b6b1f94a325807ace17bd1997762307790587` | Passed |

## Conclusion and remaining gate

CUDA 12.9.2 compilation, native GPU execution and production-runtime driver
injection are compatible with this machine and working tree. This removes the
specific audit reservation that only CUDA 12.8.1 had documented physical-GPU
execution.

Before treating the result as release evidence, commit the changes and run the
manual or scheduled `dronegs-gpu-nightly.yml` workflow with
`DRONEGS_GPU_CI=true`. Preserve its commit-scoped `gpu-validation.log` artifact;
that artifact ties the same checks to an immutable source revision.
