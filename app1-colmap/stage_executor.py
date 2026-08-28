"""CLI composition root for one-shot COLMAP stage Jobs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

def main() -> int:
    parser = argparse.ArgumentParser(description="Execute one bounded COLMAP stage")
    parser.add_argument(
        "stage",
        choices=(
            "reconstruction",
            "gaussian_training",
            "gaussian_filtering",
            "rasterization",
            "gaussian_viewer",
        ),
    )
    args = parser.parse_args()
    if __name__ == "__main__" and args.stage == "gaussian_viewer":
        from shared.gstile_defaults import configure_gstile_process

        configure_gstile_process()
    # Import only after the dedicated viewer Job has established its policy.
    # Other stages and programmatic hosts retain their existing BLAS runtime.
    from colmap_worker.stage_executor import (
        run_gaussian_filtering_stage,
        run_gaussian_viewer_stage,
        run_gaussian_training_stage,
        run_rasterization_stage,
        run_reconstruction_stage,
    )
    from shared.stage_execution import execute_one_shot_stage

    if args.stage == "reconstruction":
        execute_one_shot_stage("reconstruction", run_reconstruction_stage)
    elif args.stage == "gaussian_training":
        execute_one_shot_stage("gaussian_training", run_gaussian_training_stage)
    elif args.stage == "gaussian_filtering":
        execute_one_shot_stage("gaussian_filtering", run_gaussian_filtering_stage)
    elif args.stage == "rasterization":
        execute_one_shot_stage("rasterization", run_rasterization_stage)
    else:
        execute_one_shot_stage("gaussian_viewer", run_gaussian_viewer_stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
