#!/usr/bin/env python3
"""Compare controlled DroneGS I/O tuning with exact PLY parity."""

from __future__ import annotations

import argparse
from pathlib import Path

from gaussian_comparison_cli import expose_gaussian_training, publish_report

expose_gaussian_training(__file__)

from gaussian_training.qualification import (  # noqa: E402
    compare_performance_manifests,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = compare_performance_manifests(args.manifest)
    publish_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
