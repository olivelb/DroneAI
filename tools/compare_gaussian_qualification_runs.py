#!/usr/bin/env python3
"""Compare controlled reference/AbsGrad DroneGS qualification runs."""

from __future__ import annotations

import argparse
from pathlib import Path

from gaussian_comparison_cli import expose_gaussian_training, publish_report

expose_gaussian_training(__file__)

from gaussian_training.qualification import (  # noqa: E402
    compare_qualification_manifests,
)


EXPECTED_PROFILES = (
    "reference-absolute",
    "reference-absolute-absgrad025",
    "reference-absolute-absgrad050",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Allow fewer than the three versioned qualification profiles.",
    )
    args = parser.parse_args()
    report = compare_qualification_manifests(
        args.manifest,
        expected_profiles=None if args.allow_partial else EXPECTED_PROFILES,
    )
    publish_report(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
