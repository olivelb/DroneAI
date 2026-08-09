"""CLI composition root for one-shot COLMAP stage Jobs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from colmap_worker.stage_executor import (
    run_gaussian_filtering_stage,
    run_gaussian_training_stage,
    run_reconstruction_stage,
)
from shared.stage_execution import execute_one_shot_stage


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one bounded COLMAP stage")
    parser.add_argument(
        "stage",
        choices=("reconstruction", "gaussian_training", "gaussian_filtering"),
    )
    args = parser.parse_args()
    if args.stage == "reconstruction":
        execute_one_shot_stage("reconstruction", run_reconstruction_stage)
    elif args.stage == "gaussian_training":
        execute_one_shot_stage("gaussian_training", run_gaussian_training_stage)
    else:
        execute_one_shot_stage("gaussian_filtering", run_gaussian_filtering_stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
