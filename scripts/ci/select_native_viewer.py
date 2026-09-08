"""Select native viewer qualification; unknown events require qualification."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath

from scripts.ci.changed_paths import event_changed_paths

CONTROL_PATHS = {
    ".github/workflows/native-viewer-windows.yml",
    "scripts/ci/select_native_viewer.py", "scripts/ci/changed_paths.py",
    "scripts/ci/check_selected_jobs.py", "docs/contracts/gstile-v1.md",
}


def viewer_required(paths: list[str] | None) -> bool:
    if paths is None:
        return True
    return any(
        (path := PurePosixPath(raw).as_posix().removeprefix("./")) in CONTROL_PATHS
        or path.startswith(("native-viewer/", "shared/gstile_", "app1-colmap/gaussian_tiles/", "tests/fixtures/gstile/"))
        for raw in paths
    )


def main() -> None:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        paths = event_changed_paths(os.environ.get("GITHUB_EVENT_NAME", ""), event)
    except (OSError, ValueError, KeyError, TypeError):
        paths = None
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(f"viewer_required={str(viewer_required(paths)).lower()}\n")


if __name__ == "__main__":
    main()
