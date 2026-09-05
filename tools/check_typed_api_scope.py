#!/usr/bin/env python3
"""Reject new API modules omitted from the declared mypy scope."""
import json
from pathlib import Path
import subprocess
import re


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    commands = subprocess.check_output(["make", "-n", "typecheck"], cwd=root, text=True)
    typed = set(re.findall(r"app4-dashboard/api/[a-zA-Z0-9_/]+\.py", commands))
    baseline = set(json.loads((root / "tools/typed_api_exclusions.json").read_text()))
    actual = {str(path.relative_to(root)) for path in (root / "app4-dashboard/api").rglob("*.py")}
    missing = actual - typed - baseline
    stale = baseline - actual
    if missing or stale:
        print(json.dumps({"untyped_new_files": sorted(missing), "stale_exclusions": sorted(stale)}, indent=2))
        return 1
    print(f"API type scope: {len(actual & typed)} modules checked, {len(baseline)} explicit existing exclusions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
