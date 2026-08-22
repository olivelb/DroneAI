from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_build_gstiles_cli_loads_repository_imports() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/build_gstiles.py", "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Build an immutable GSTile v1 bundle" in completed.stdout
    assert "--temporary-root" in completed.stdout
    assert "--progress-jsonl" in completed.stdout
