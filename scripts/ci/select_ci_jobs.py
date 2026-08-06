"""Select CI jobs from pull-request paths without external actions.

Pushes to ``main`` and manual runs deliberately execute every scope. Pull
requests only execute jobs whose runtime or contract is affected by the diff.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import PurePosixPath
from typing import Final

SCOPES: Final = (
    "python",
    "docs",
    "dronegs",
    "migrations",
    "frontend",
    "containers",
    "helm",
)
CONTROL_PATHS: Final = {
    ".github/workflows/ci.yml",
    "scripts/ci/select_ci_jobs.py",
    "tests/test_ci_change_scopes.py",
}


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(f"{directory}/")


def classify_paths(paths: list[str]) -> dict[str, bool]:
    """Return the minimal safe job set for normalized repository paths."""

    normalized = set()
    for path in paths:
        normalized_path = PurePosixPath(path).as_posix()
        if normalized_path.startswith("./"):
            normalized_path = normalized_path[2:]
        normalized.add(normalized_path)
    selected = {scope: False for scope in SCOPES}
    if normalized & CONTROL_PATHS:
        return {scope: True for scope in SCOPES}

    for path in normalized:
        is_python = path.endswith(".py") and any(
            _under(path, directory)
            for directory in (
                "app1-colmap",
                "app2-ia",
                "app3-processing",
                "app4-dashboard/api",
                "shared",
                "alembic",
                "tests",
                "tools",
                "scripts/ci",
            )
        )
        if is_python or path in {"Makefile", "pyproject.toml"} or _under(path, "requirements"):
            selected["python"] = True
        if path.endswith(".md") or path in {
            "tools/check_markdown_links.py",
            "tests/test_markdown_links.py",
        }:
            selected["docs"] = True
        if _under(path, "app1-colmap/dronegs") and not path.endswith(".md"):
            selected["dronegs"] = True
        if _under(path, "alembic") or path in {
            "alembic.ini",
            "shared/config.py",
            "shared/database.py",
            "requirements/dev.in",
            "requirements/dev.txt",
        }:
            selected["migrations"] = True
        if _under(path, "app4-dashboard/frontend"):
            selected["frontend"] = True
        if (
            _under(path, "shared")
            or _under(path, "app3-processing")
            or _under(path, "app4-dashboard/api")
            or _under(path, "alembic")
            or path
            in {
                ".dockerignore",
                "alembic.ini",
                "app3-processing/Dockerfile",
                "app4-dashboard/api/Dockerfile",
                "requirements/api.in",
                "requirements/api.txt",
                "requirements/processing.in",
                "requirements/processing.txt",
            }
        ):
            selected["containers"] = True
        if _under(path, "charts/drone-ai"):
            selected["helm"] = True

    return selected


def _pull_request_paths(event: dict[str, object]) -> list[str]:
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, dict):
        raise ValueError("pull_request event payload is missing pull_request")
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise ValueError("pull_request event payload is missing base/head")
    base_sha = base.get("sha")
    head_sha = head.get("sha")
    if not isinstance(base_sha, str) or not isinstance(head_sha, str):
        raise ValueError("pull_request event payload is missing base/head SHA")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "--no-renames",
            "--diff-filter=ACMD",
            f"{base_sha}...{head_sha}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def main() -> int:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is required")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "pull_request":
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if not event_path:
            raise RuntimeError("GITHUB_EVENT_PATH is required for pull requests")
        with open(event_path, encoding="utf-8") as handle:
            selected = classify_paths(_pull_request_paths(json.load(handle)))
    else:
        selected = {scope: True for scope in SCOPES}

    with open(output_path, "a", encoding="utf-8") as handle:
        for scope in SCOPES:
            handle.write(f"{scope}={str(selected[scope]).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
