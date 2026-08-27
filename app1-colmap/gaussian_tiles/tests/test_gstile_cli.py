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
    assert "--lod-proxy-size" in completed.stdout
    assert "--lod-proxy-strategy" in completed.stdout
    assert "--pack-workers" in completed.stdout
    assert "--pack-pending-bytes" in completed.stdout


def test_repack_gstiles_cli_loads_repository_imports() -> None:
    completed = subprocess.run(
        [sys.executable, "tools/repack_gstiles.py", "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Losslessly aggregate" in completed.stdout
    assert "--pack-target-bytes" in completed.stdout
