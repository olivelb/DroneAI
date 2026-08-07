"""Gate physical-GPU qualification on explicit or GPU-relevant changes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import PurePosixPath
from typing import Any, Final

CUDA_VERSION_LINE: Final = re.compile(r"^([+-])FROM\s+nvidia/cuda:([^\s]+)")
ARCHITECTURE_LINE: Final = re.compile(
    r"DRONEGS_CUDA_ARCHITECTURES|CMAKE_CUDA_ARCHITECTURES|"
    r"(?:^|[;\"\s])\d{2,3}-(?:real|virtual)(?:[;\"\s]|$)"
)
CTEST_LINE: Final = re.compile(
    r"add_test|enable_testing|DRONEGS_BUILD_TESTS|tests/|_tests|"
    r"find_package\(Python3"
)
DIFF_FILES: Final = (
    "app1-colmap/Dockerfile.base",
    "app1-colmap/Dockerfile.local-gaussian",
    "app1-colmap/dronegs/Dockerfile",
    "app1-colmap/dronegs/CMakeLists.txt",
)
VALIDATION_HARNESS: Final = "scripts/ci/validate_cuda_containers.sh"
GPU_HEADER_PATHS: Final = {
    "app1-colmap/dronegs/include/dronegs/loss.hpp",
    "app1-colmap/dronegs/include/dronegs/ordered_training.hpp",
    "app1-colmap/dronegs/include/dronegs/rasterization.hpp",
    "app1-colmap/dronegs/include/dronegs/training.hpp",
    "app1-colmap/dronegs/include/dronegs/types.hpp",
}


def _changed_semantics(diff_lines: list[str], pattern: re.Pattern[str]) -> bool:
    old: set[str] = set()
    new: set[str] = set()
    for line in diff_lines:
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        value = line[1:].strip()
        if pattern.search(value):
            (old if line[0] == "-" else new).add(value)
    return old != new


def gpu_validation_reason(paths: list[str], diff_lines: list[str]) -> str | None:
    """Return why a pull request needs the physical-GPU test suite."""

    normalized = {PurePosixPath(path).as_posix().removeprefix("./") for path in paths}

    old_cuda: set[str] = set()
    new_cuda: set[str] = set()
    for line in diff_lines:
        match = CUDA_VERSION_LINE.match(line)
        if match:
            (old_cuda if match.group(1) == "-" else new_cuda).add(match.group(2))
    if old_cuda != new_cuda:
        return "cuda-version-change"

    if _changed_semantics(diff_lines, ARCHITECTURE_LINE):
        return "gpu-architecture-change"
    if _changed_semantics(diff_lines, CTEST_LINE):
        return "ctest-definition-change"
    if VALIDATION_HARNESS in normalized:
        return "gpu-validation-harness-change"
    if any(path.startswith("app1-colmap/dronegs/tests/") for path in normalized):
        return "ctest-source-change"
    if normalized & GPU_HEADER_PATHS:
        return "cuda-interface-change"
    if any(
        path.startswith("app1-colmap/dronegs/cuda/")
        or (path.startswith("app1-colmap/dronegs/") and PurePosixPath(path).suffix in {".cu", ".cuh"})
        for path in normalized
    ):
        return "cuda-source-change"
    return None


def _event_range(event_name: str, event: dict[str, Any]) -> tuple[str, str] | None:
    if event_name == "pull_request":
        return str(event["pull_request"]["base"]["sha"]), str(event["pull_request"]["head"]["sha"])
    return None


def _git_diff(base_sha: str, head_sha: str, *, names_only: bool) -> list[str]:
    command = ["git", "diff"]
    if names_only:
        command.extend(["--name-only", "--no-renames", "--diff-filter=ACMD"])
    else:
        command.extend(["--unified=0", "--no-ext-diff"])
    command.append(f"{base_sha}...{head_sha}")
    if not names_only:
        command.extend(["--", *DIFF_FILES])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is required")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        reason = "manual-dispatch"
    else:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise RuntimeError("GITHUB_EVENT_PATH is required")
        with open(event_path, encoding="utf-8") as handle:
            event_range = _event_range(event_name, json.load(handle))
        reason = None
        if event_range is not None:
            paths = _git_diff(*event_range, names_only=True)
            diff_lines = _git_diff(*event_range, names_only=False)
            reason = gpu_validation_reason(paths, diff_lines)

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"gpu_required={str(reason is not None).lower()}\n")
        handle.write(f"reason={reason or 'no-gpu-relevant-change'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
