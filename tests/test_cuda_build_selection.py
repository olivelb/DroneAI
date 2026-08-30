from pathlib import Path

import pytest

from scripts.ci.changed_paths import changed_paths
from scripts.ci.cuda_change_scope import cuda_build_reason

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("path", [
    "app1-colmap/Dockerfile.base", "app1-colmap/Dockerfile.local-gaussian",
    "app1-colmap/Dockerfile", "app1-colmap/dronegs/Dockerfile",
    "app1-colmap/dronegs/CMakeLists.txt", "app1-colmap/dronegs/src/model.cpp",
    "app1-colmap/dronegs/include/dronegs/model.hpp",
    "app1-colmap/dronegs/cuda/trainer.cu", "app1-colmap/dronegs/tests/test_loss.cu",
    "setup_deps.sh", ".dockerignore", "requirements/colmap.in",
    "requirements/colmap.txt", "requirements/local-gaussian.txt",
    ".github/workflows/cuda-containers.yml",
    "scripts/ci/select_cuda_builds.py", "scripts/ci/cuda_change_scope.py",
    "scripts/ci/changed_paths.py",
    "scripts/ci/validate_cuda_containers.sh",
])
def test_any_build_input_change_requires_cuda_validation(path: str) -> None:
    assert cuda_build_reason([path]) == "cuda-build-input-change"


@pytest.mark.parametrize("path", [
    "README.md", "docs/PRODUCTION_READINESS.md",
    "app4-dashboard/frontend/app/page.tsx", "app4-dashboard/api/security.py",
])
def test_unrelated_changes_do_not_rebuild_cuda(path: str) -> None:
    assert cuda_build_reason([path]) is None


def test_deleted_and_renamed_build_inputs_are_not_hidden(tmp_path: Path) -> None:
    import subprocess

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    source = tmp_path / "app1-colmap" / "Dockerfile.base"
    source.parent.mkdir()
    source.write_text("FROM scratch\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    source.rename(tmp_path / "README.md")
    git("add", "-A")
    git("commit", "-qm", "rename")
    paths = changed_paths(base, git("rev-parse", "HEAD"), cwd=tmp_path)
    assert "app1-colmap/Dockerfile.base" in paths
    assert cuda_build_reason(paths)


def test_unknown_event_fails_closed_by_selecting_build(monkeypatch, tmp_path: Path) -> None:
    from scripts.ci.select_cuda_builds import main

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_EVENT_NAME", "unexpected")
    assert main() == 0
    assert "build_required=true" in output.read_text()


def test_explicit_cuda_architectures_reach_all_native_builds() -> None:
    dockerfile = (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8")

    assert dockerfile.count('ARG CUDA_ARCHITECTURES') == 3
    assert dockerfile.count('-DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"') == 2
    assert '-DDRONEGS_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"' in dockerfile


def test_packaging_only_inputs_rebuild_without_using_physical_gpu() -> None:
    from scripts.ci.cuda_change_scope import gpu_validation_reason

    for path in ("LICENSE", "THIRD_PARTY_NOTICES.md", "requirements/colmap.txt", "setup_deps.sh"):
        assert cuda_build_reason([path]) == "cuda-build-input-change"
        assert gpu_validation_reason([path]) is None


def test_gpu_policy_and_unit_test_changes_do_not_rebuild_cuda_images() -> None:
    for path in (
        ".github/workflows/dronegs-gpu-qualification.yml",
        "scripts/ci/select_gpu_validation.py",
        "tests/test_gpu_validation_selection.py",
        "tests/test_ci_selected_gate.py",
    ):
        assert cuda_build_reason([path]) is None
