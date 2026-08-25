"""Shared bootstrap and structured progress helpers for GSTile CLIs."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any


def configure_repository_imports(script_file: str | Path) -> None:
    """Expose repository and COLMAP modules when a tool runs as a script."""

    repository_root = Path(script_file).resolve().parents[1]
    for import_root in (repository_root, repository_root / "app1-colmap"):
        root = str(import_root)
        if root not in sys.path:
            sys.path.insert(0, root)


def jsonl_progress_callback(
    enabled: bool,
) -> Callable[[dict[str, Any]], None] | None:
    """Return a deterministic stderr JSONL reporter when requested."""

    if not enabled:
        return None

    def report(event: dict[str, Any]) -> None:
        print(json.dumps(event, sort_keys=True), file=sys.stderr, flush=True)

    return report
