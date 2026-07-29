#!/usr/bin/env python3
"""Run a repeatable benchmark suite against one or more Gaussian trainers."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
for import_path in (REPO_ROOT, APP1_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from gaussian_training.benchmark import (  # noqa: E402
    expand_command,
    load_benchmark_suite,
    run_benchmark_suite,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path, help="Versioned benchmark suite JSON")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--backend", action="append", dest="backends")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="Create a portable .tar.gz containing reports, logs and artifacts.",
    )
    return parser.parse_args()


def default_output_root() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return REPO_ROOT / ".local-workspaces" / "dronegs-benchmarks" / stamp


def dry_run_payload(suite, output_root: Path, backends, cases) -> dict:
    runs = []
    for case in suite.cases:
        if cases and case.name not in cases:
            continue
        for backend in suite.backends:
            if backends and backend.name not in backends:
                continue
            for repetition in range(1, suite.repetitions + 1):
                run_dir = output_root / suite.name / case.name / backend.name / f"run-{repetition:03d}"
                trainer_output_dir = run_dir / "artifacts"
                runs.append({
                    "case": case.name,
                    "backend": backend.name,
                    "repetition": repetition,
                    "command": expand_command(
                        backend, case, trainer_output_dir, repetition
                    ),
                    "output": str(trainer_output_dir),
                })
    if not runs:
        raise ValueError("benchmark selection produced no runs")
    return {"suite": suite.name, "dry_run": True, "runs": runs}


def main() -> int:
    args = parse_args()
    suite = load_benchmark_suite(args.suite)
    output_root = (args.output_root or default_output_root()).resolve()
    selected_backends = set(args.backends or [])
    selected_cases = set(args.cases or [])
    if args.dry_run:
        report = dry_run_payload(suite, output_root, selected_backends, selected_cases)
    else:
        report = run_benchmark_suite(
            suite, output_root, selected_backends or None, selected_cases or None
        )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.bundle and not args.dry_run:
        suite_root = output_root / suite.name
        archive = shutil.make_archive(
            str(suite_root),
            "gztar",
            root_dir=suite_root.parent,
            base_dir=suite_root.name,
        )
        print(f"Benchmark evidence bundle: {archive}", file=sys.stderr)
    summaries = report.get("summaries", [])
    return 0 if all(
        item.get("successful_runs", 0) == item.get("runs", -1)
        for item in summaries
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
