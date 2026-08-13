"""Shared command-line plumbing for Gaussian comparison reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping


def expose_gaussian_training(source_file: str) -> None:
    """Make the repository-local app1 package importable by a tool script."""

    app1_root = Path(source_file).resolve().parents[1] / "app1-colmap"
    app1_root_text = str(app1_root)
    if app1_root_text not in sys.path:
        sys.path.insert(0, app1_root_text)


def publish_report(report: Mapping[str, Any], output: Path) -> None:
    """Persist and print one canonical human-readable comparison report."""

    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
