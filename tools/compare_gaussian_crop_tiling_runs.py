#!/usr/bin/env python3
"""Compare fixed and adaptive native-crop tiling DroneGS runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from gaussian_comparison_cli import expose_gaussian_training, publish_report

expose_gaussian_training(__file__)

from gaussian_training.qualification import (  # noqa: E402
    compare_native_crop_tiling_manifests,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs=2, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = compare_native_crop_tiling_manifests(args.manifest)
    publish_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
