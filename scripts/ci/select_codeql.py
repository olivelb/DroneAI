"""Select CodeQL languages from source changes; unknown source runs all."""

from __future__ import annotations

import json
import os
from pathlib import PurePosixPath
from typing import Any, Final

from scripts.ci.changed_paths import event_changed_paths

CONTROL_PATHS: Final = {
    ".github/workflows/codeql.yml",
    "scripts/ci/select_codeql.py",
    "scripts/ci/changed_paths.py",
    "scripts/ci/check_selected_jobs.py",
}
PYTHON_SUFFIXES: Final = {".py", ".pyi"}
JAVASCRIPT_SUFFIXES: Final = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
CPP_SUFFIXES: Final = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".cu", ".cuh"}
SOURCE_ROOTS: Final = ("app1-colmap/", "app2-ia/", "app4-dashboard/", "shared/", "tools/")


def codeql_scopes(paths: list[str]) -> dict[str, bool]:
    python = False
    javascript = False
    c_cpp = False
    for raw_path in paths:
        path = PurePosixPath(raw_path).as_posix().removeprefix("./")
        suffix = PurePosixPath(path).suffix
        if path in CONTROL_PATHS:
            python = javascript = c_cpp = True
        elif suffix in PYTHON_SUFFIXES:
            python = True
        elif suffix in JAVASCRIPT_SUFFIXES:
            javascript = True
        elif suffix in CPP_SUFFIXES:
            c_cpp = True
        elif path.startswith(SOURCE_ROOTS) and suffix not in {
            ".md", ".css", ".scss", ".json", ".yaml", ".yml", ".sh",
        }:
            # New source representation: scan all until ownership is explicit.
            python = javascript = c_cpp = True
    return {
        "python": python,
        "javascript": javascript,
        "c_cpp": c_cpp,
        "any": python or javascript or c_cpp,
    }


def main() -> int:
    output_path = os.environ["GITHUB_OUTPUT"]
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        selected = {"python": True, "javascript": True, "c_cpp": True, "any": True}
    else:
        event: dict[str, Any] = {}
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if event_path:
            try:
                with open(event_path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    event = loaded
            except (OSError, json.JSONDecodeError):
                pass
        paths = event_changed_paths(event_name, event)
        selected = (
            {"python": True, "javascript": True, "c_cpp": True, "any": True}
            if paths is None else codeql_scopes(paths)
        )
    with open(output_path, "a", encoding="utf-8") as handle:
        for output in ("python", "javascript", "c_cpp", "any"):
            handle.write(f"{output}={str(selected[output]).lower()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
