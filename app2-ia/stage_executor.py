"""CLI composition root for the bounded AI detection stage."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
for module_path in (ROOT_DIR, APP_DIR):
    if str(module_path) not in sys.path:
        sys.path.append(str(module_path))

from detection_stage import run_detection_stage
from shared.stage_execution import execute_one_shot_stage


def main() -> int:
    execute_one_shot_stage("detection", run_detection_stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
