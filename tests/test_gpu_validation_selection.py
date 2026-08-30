import pytest

from scripts.ci.select_gpu_validation import gpu_validation_reason


@pytest.mark.parametrize("path", [
    "app1-colmap/dronegs/cuda/trainer.cu",
    "app1-colmap/dronegs/include/dronegs/training.hpp",
    "app1-colmap/dronegs/src/model.cpp",
    "app1-colmap/dronegs/Dockerfile",
    "app1-colmap/dronegs/CMakeLists.txt",
    "app1-colmap/Dockerfile.base",
    "app1-colmap/Dockerfile.local-gaussian",
    "scripts/ci/validate_cuda_containers.sh",
    "scripts/ci/select_gpu_validation.py",
    "scripts/ci/cuda_change_scope.py",
    "scripts/ci/changed_paths.py",
    ".github/workflows/dronegs-gpu-qualification.yml",
])
def test_exercised_gpu_or_harness_change_requests_physical_validation(path: str) -> None:
    assert gpu_validation_reason([path]) == "gpu-validation-input-change"


@pytest.mark.parametrize("path", [
    "docs/PRODUCTION_READINESS.md",
    "docs/dronegs/GPL_COMPONENTS.md",
    "THIRD_PARTY_NOTICES.md",
    "LICENSE",
    "requirements/colmap.txt",
    "requirements/local-gaussian.txt",
    "setup_deps.sh",
    "app1-colmap/colmap-local/src/mapper.cc",
    "app4-dashboard/frontend/app/page.tsx",
])
def test_input_not_exercised_by_gpu_suite_does_not_request_it(path: str) -> None:
    assert gpu_validation_reason([path]) is None


def test_hosted_build_policy_and_unit_tests_do_not_consume_gpu_runner() -> None:
    for path in (
        ".github/workflows/cuda-containers.yml",
        "scripts/ci/select_cuda_builds.py",
        "tests/test_cuda_build_selection.py",
        "tests/test_gpu_validation_selection.py",
        "tests/test_ci_selected_gate.py",
    ):
        assert gpu_validation_reason([path]) is None
