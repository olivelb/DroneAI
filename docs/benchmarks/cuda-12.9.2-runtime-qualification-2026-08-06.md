# CUDA 12.9.2 runtime qualification — 2026-08-06

## Purpose and scope

This record closes the audit gap between the CUDA 12.9.2 image contracts and
their execution on physical NVIDIA hardware. It validates the native DroneGS
test suites and NVIDIA driver injection in both production runtime bases. It is
not a dataset-quality benchmark, a complete DroneAI deployment test or an
immutable release attestation.

The confirming run used clean Git commit
`1eeb49ef501482b9e745036a0ef557348c53e922`, which contains the audit-hardening
changes, qualification reporting and COLMAP container hardening. This ties the
local physical-GPU result to an immutable source revision. It is still a local
qualification rather than a retained GitHub Actions artifact.

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
| `dronegs_core_tests` | Passed | 0.01 s |
| `dronegs_rasterization_tests` | Passed | 0.00 s |
| `dronegs_cuda_tests` | Passed | 0.22 s |
| `dronegs_rasterization_cuda_tests` | Passed | 0.17 s |
| `dronegs_training_tests` | Passed | 0.21 s |
| `dronegs_lpips_tool_tests` | Passed | 0.05 s |

CTest reported 100% success: 6 tests passed, 0 failed, with 0.66 seconds of
test execution time. With Docker base layers already cached, the complete
post-commit container build, native execution and runtime-smoke command
completed in 27.9 seconds.

The 7,564-byte local log contains 156 lines and has SHA-256
`99b3972e35de6b66772c9a4c01f27cec04c92957861f8c8c2e9bad0fd45b5ea5`.

## Production runtime driver injection

Docker successfully exposed the RTX 4070 and driver 610.62 inside both runtime
images:

| Runtime image | Pulled digest | Result |
|---|---|---|
| `nvidia/cuda:12.9.2-cudnn-runtime-ubuntu24.04` | `sha256:070f8f2672df1b05b84c0409a5fd1d54ddfd646e5b9d8dee7878131271b563fc` | Passed |
| `nvidia/cuda:12.9.2-runtime-ubuntu24.04` | `sha256:6d2a0dabc50c3bf14d27fc66822b6b1f94a325807ace17bd1997762307790587` | Passed |

## Conclusion and remaining gate

CUDA 12.9.2 compilation, native GPU execution and production-runtime driver
injection are compatible with this machine and commit `1eeb49e`. This removes
the specific audit reservation that only CUDA 12.8.1 had documented
physical-GPU execution.

For a centrally retained release artifact, push the qualified commit and run
the manual or scheduled `dronegs-gpu-nightly.yml` workflow with
`DRONEGS_GPU_CI=true`. Preserve its commit-scoped `gpu-validation.log` artifact.
