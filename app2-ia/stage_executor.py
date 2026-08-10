"""CLI composition root for the bounded AI detection stage."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = Path(__file__).resolve().parent
for module_path in (ROOT_DIR, APP_DIR):
    if str(module_path) not in sys.path:
        sys.path.append(str(module_path))

from detection_shard_stage import (
    run_detection_finalizer,
    run_detection_shard_subtask,
)
from detection_stage import run_detection_stage
from shared.stage_execution import execute_one_shot_stage, execute_stage_subtask


def main() -> int:
    mode = os.getenv("DRONEAI_DETECTION_EXECUTION_MODE", "monolithic").strip().lower()
    if mode == "monolithic":
        execute_one_shot_stage("detection", run_detection_stage)
    elif mode == "shard":
        execute_stage_subtask("detection", run_detection_shard_subtask)
    elif mode == "finalizer":
        execute_one_shot_stage("detection", run_detection_finalizer)
    else:
        raise ValueError(f"Unsupported detection execution mode: {mode!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
