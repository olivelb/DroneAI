"""Gate expensive CUDA builds on explicit dispatch or version changes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Final

CUDA_VERSION_LINE: Final = re.compile(r"^([+-])FROM\s+nvidia/cuda:([^\s]+)")
COLMAP_VERSION_LINE: Final = re.compile(r"^([+-])\s*COLMAP_TAG\s*=\s*['\"]([^'\"]+)['\"]")
VERSION_FILES: Final = (
    "app1-colmap/Dockerfile.base",
    "app1-colmap/Dockerfile.local-gaussian",
    "setup_deps.sh",
)


def version_change_reason(diff_lines: list[str]) -> str | None:
    """Identify authoritative CUDA/COLMAP version-line changes."""

    old_cuda: set[str] = set()
    new_cuda: set[str] = set()
    old_colmap: set[str] = set()
    new_colmap: set[str] = set()
    for line in diff_lines:
        cuda_match = CUDA_VERSION_LINE.match(line)
        if cuda_match:
            (old_cuda if cuda_match.group(1) == "-" else new_cuda).add(cuda_match.group(2))
        colmap_match = COLMAP_VERSION_LINE.match(line)
        if colmap_match:
            (old_colmap if colmap_match.group(1) == "-" else new_colmap).add(colmap_match.group(2))

    if old_cuda != new_cuda:
        return "cuda-version-change"
    if old_colmap != new_colmap:
        return "colmap-version-change"
    return None


def _event_range(event_name: str, event: dict[str, Any]) -> tuple[str, str] | None:
    if event_name == "pull_request":
        return str(event["pull_request"]["base"]["sha"]), str(event["pull_request"]["head"]["sha"])
    return None


def _version_diff(base_sha: str, head_sha: str) -> list[str]:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-ext-diff",
            f"{base_sha}...{head_sha}",
            "--",
            *VERSION_FILES,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


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
        reason = None if event_range is None else version_change_reason(_version_diff(*event_range))

    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write(f"build_required={str(reason is not None).lower()}\n")
        handle.write(f"reason={reason or 'no-version-change'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
