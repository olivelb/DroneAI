from __future__ import annotations

from scripts.ci.select_gpu_validation import gpu_validation_reason


def test_cuda_version_change_requests_physical_gpu_validation() -> None:
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/Dockerfile"],
            [
                "-FROM nvidia/cuda:12.9.2-devel-ubuntu24.04",
                "+FROM nvidia/cuda:13.0.0-devel-ubuntu24.04",
            ],
        )
        == "cuda-version-change"
    )


def test_new_gpu_architecture_requests_physical_gpu_validation() -> None:
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/CMakeLists.txt"],
            [
                '-        "75-real;80-real;86-real;89-real")',
                '+        "75-real;80-real;86-real;89-real;120-real")',
            ],
        )
        == "gpu-architecture-change"
    )


def test_ctest_definition_or_source_change_requests_validation() -> None:
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/CMakeLists.txt"],
            [
                "-    add_test(NAME dronegs_cuda_tests COMMAND dronegs_cuda_tests)",
                "+    add_test(NAME dronegs_cuda_tests COMMAND dronegs_cuda_tests --strict)",
            ],
        )
        == "ctest-definition-change"
    )
    assert gpu_validation_reason(["app1-colmap/dronegs/tests/test_training.cu"], []) == "ctest-source-change"


def test_cuda_source_or_validation_harness_change_requests_validation() -> None:
    assert gpu_validation_reason(["app1-colmap/dronegs/cuda/trainer.cu"], []) == "cuda-source-change"
    assert gpu_validation_reason(["app1-colmap/dronegs/include/dronegs/training.hpp"], []) == "cuda-interface-change"
    assert gpu_validation_reason(["scripts/ci/validate_cuda_containers.sh"], []) == "gpu-validation-harness-change"


def test_unrelated_changes_do_not_request_physical_gpu_validation() -> None:
    assert gpu_validation_reason(["docs/PRODUCTION_READINESS.md"], []) is None
    assert (
        gpu_validation_reason(
            [
                ".github/workflows/dronegs-gpu-qualification.yml",
                "scripts/ci/select_gpu_validation.py",
            ],
            [],
        )
        is None
    )
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/Dockerfile"],
            ["-RUN apt-get update", "+RUN apt-get update && apt-get upgrade -y"],
        )
        is None
    )
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/CMakeLists.txt"],
            ["-project(DroneGS VERSION 0.5.0", "+project(DroneGS VERSION 0.6.0"],
        )
        is None
    )
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/CMakeLists.txt"],
            ["-set(CMAKE_CXX_STANDARD 23)", "+set(CMAKE_CXX_STANDARD 26)"],
        )
        is None
    )


def test_reordering_same_cuda_version_or_architecture_is_ignored() -> None:
    assert (
        gpu_validation_reason(
            ["app1-colmap/dronegs/Dockerfile"],
            [
                "-FROM nvidia/cuda:12.9.2-devel-ubuntu24.04 AS first",
                "+FROM nvidia/cuda:12.9.2-devel-ubuntu24.04 AS renamed",
            ],
        )
        is None
    )
