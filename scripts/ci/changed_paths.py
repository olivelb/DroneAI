"""Fail-safe changed-path extraction shared by conditional CI selectors."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def changed_paths(base: str, head: str, *, cwd: Path | None = None) -> list[str]:
    """Return both additions and deletions; a rename is deliberately D+A."""
    if not all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in (base, head)):
        raise ValueError("Expected full Git commit SHAs")
    result = subprocess.run(
        ["git", "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff",
         f"{base}...{head}", "--"],
        cwd=cwd, check=True, capture_output=True, text=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def _nested_string(payload: dict[str, Any], *keys: str) -> str | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) else None


def event_changed_paths(
    event_name: str,
    event: dict[str, Any],
    *,
    cwd: Path | None = None,
) -> list[str] | None:
    """Return None when an event cannot be classified, never an exemption."""
    if event_name == "pull_request":
        base = _nested_string(event, "pull_request", "base", "sha")
        head = _nested_string(event, "pull_request", "head", "sha")
    elif event_name == "merge_group":
        base = _nested_string(event, "merge_group", "base_sha")
        head = _nested_string(event, "merge_group", "head_sha")
    else:
        return None
    if base is None or head is None:
        return None
    try:
        return changed_paths(base, head, cwd=cwd)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
