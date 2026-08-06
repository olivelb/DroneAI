from __future__ import annotations

from scripts.ci.select_cuda_builds import version_change_reason


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
