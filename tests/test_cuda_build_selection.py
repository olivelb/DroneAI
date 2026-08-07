from __future__ import annotations

from pathlib import Path

from scripts.ci.select_cuda_builds import version_change_reason


ROOT = Path(__file__).resolve().parents[1]


def test_cuda_base_tag_change_requests_expensive_builds() -> None:
    assert (
        version_change_reason(
            [
                "-FROM nvidia/cuda:12.9.2-devel-ubuntu24.04 AS builder",
                "+FROM nvidia/cuda:13.0.0-devel-ubuntu24.04 AS builder",
            ]
        )
        == "cuda-version-change"
    )


def test_colmap_tag_change_requests_expensive_builds() -> None:
    assert version_change_reason(['-COLMAP_TAG="4.1.1"', '+COLMAP_TAG="4.2.0"']) == "colmap-version-change"


def test_ordinary_cuda_source_and_dockerfile_changes_do_not_request_builds() -> None:
    assert version_change_reason(["+RUN apt-get update", "+int rasterize_gaussians();"]) is None


def test_comments_mentioning_versions_do_not_request_builds() -> None:
    assert version_change_reason(["+# CUDA 13.0.0", "+# COLMAP_TAG=4.2.0"]) is None


def test_reordered_stages_with_the_same_cuda_version_do_not_request_builds() -> None:
    assert (
        version_change_reason(
            [
                "-FROM nvidia/cuda:12.9.2-devel-ubuntu24.04 AS first",
                "+FROM nvidia/cuda:12.9.2-devel-ubuntu24.04 AS second",
            ]
        )
        is None
    )


def test_explicit_cuda_architectures_reach_all_native_builds() -> None:
    dockerfile = (ROOT / "app1-colmap" / "Dockerfile.base").read_text(encoding="utf-8")
    ceres = (ROOT / "app1-colmap" / "ceres-solver" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )

    assert dockerfile.count('ARG CUDA_ARCHITECTURES') == 3
    assert '-DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"' in dockerfile
    assert '-DDRONEGS_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"' in dockerfile
    assert "if (NOT CMAKE_CUDA_ARCHITECTURES)" in ceres
