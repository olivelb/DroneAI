"""Small, deterministic JSON file primitives shared by workers and tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: str | os.PathLike[str], payload: Any) -> None:
    """Write formatted JSON through an atomic same-directory replacement."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, destination)
