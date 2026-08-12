#!/usr/bin/env python3
"""Compare controlled reference/AbsGrad DroneGS qualification runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
APP1_ROOT = REPO_ROOT / "app1-colmap"
if str(APP1_ROOT) not in sys.path:
    sys.path.insert(0, str(APP1_ROOT))

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
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
