"""Shared dependency closure for CUDA builds and physical GPU qualification."""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any, Final

from scripts.ci.changed_paths import event_changed_paths

BUILD_INPUTS: Final = {
    ".dockerignore", "setup_deps.sh", "setup.sh", "build_and_deploy.sh",
    ".github/workflows/cuda-containers.yml",
    "scripts/ci/select_cuda_builds.py", "scripts/ci/cuda_change_scope.py",
    "scripts/ci/changed_paths.py", "scripts/ci/validate_cuda_containers.sh",
    "docs/dronegs/GPL_COMPONENTS.md", "THIRD_PARTY_NOTICES.md", "LICENSE",
}
BUILD_PREFIXES: Final = (
    "app1-colmap/Dockerfile", "app1-colmap/dronegs/",
    "app1-colmap/colmap-local/", "app1-colmap/colmap-deps/",
    "app1-colmap/ceres-solver/", "requirements/colmap.",
    "requirements/local-gaussian.", "scripts/build/",
)
GPU_INPUTS: Final = {
    ".github/workflows/dronegs-gpu-qualification.yml",
    "scripts/ci/select_gpu_validation.py",
    "scripts/ci/cuda_change_scope.py",
    "scripts/ci/changed_paths.py",
    "scripts/ci/validate_cuda_containers.sh",
}
GPU_DOCKERFILES: Final = {
    "app1-colmap/Dockerfile.base",
    "app1-colmap/Dockerfile.local-gaussian",
    "app1-colmap/dronegs/Dockerfile",
}


def cuda_build_reason(paths: list[str]) -> str | None:
    """Any change to a copied/native/build input invalidates the evidence.

    Inspect paths, not version lines: flags, hashes, deletions and stage
    ordering can change a binary without changing a version number.
    """
    for raw_path in paths:
        path = PurePosixPath(raw_path).as_posix().removeprefix("./")
        if path in BUILD_INPUTS or path.startswith(BUILD_PREFIXES):
            return "cuda-build-input-change"
    return None



def gpu_validation_reason(paths: list[str]) -> str | None:
    """Select only inputs exercised by native GPU tests or runtime smoke."""
    for raw_path in paths:
        path = PurePosixPath(raw_path).as_posix().removeprefix("./")
        if path in GPU_INPUTS or path in GPU_DOCKERFILES:
            return "gpu-validation-input-change"
        if path.startswith("app1-colmap/dronegs/") and not path.endswith(".md"):
            return "gpu-validation-input-change"
    return None


def select_requirement(output_name: str, classifier: Any = cuda_build_reason) -> int:
    output_path = os.environ["GITHUB_OUTPUT"]
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    reason: str | None
    if event_name == "workflow_dispatch":
        reason = "manual-dispatch"
    else:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        event: dict[str, Any] = {}
        if event_path:
            try:
                with open(event_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    event = loaded
            except (OSError, json.JSONDecodeError):
                pass
        paths = event_changed_paths(event_name, event)
        reason = "unclassified-event" if paths is None else classifier(paths)
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"{output_name}={str(reason is not None).lower()}\n")
        handle.write(f"reason={reason or 'no-cuda-build-input-change'}\n")
    return 0
