#!/usr/bin/env python3
"""Losslessly aggregate an immutable GSTile bundle into fewer packs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLMAP_ROOT = REPOSITORY_ROOT / "app1-colmap"
for import_root in (REPOSITORY_ROOT, COLMAP_ROOT):
    root = str(import_root)
    if root not in sys.path:
        sys.path.insert(0, root)

from gaussian_tiles import repack_gstile_bundle  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_bundle", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--pack-target-bytes", type=int, required=True)
    parser.add_argument(
        "--progress-jsonl",
        action="store_true",
        help="emit structured repack progress to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    progress_callback = None
    if arguments.progress_jsonl:
        progress_callback = lambda event: print(
            json.dumps(event, sort_keys=True), file=sys.stderr, flush=True
        )
    result = repack_gstile_bundle(
        arguments.source_bundle,
        arguments.output_directory,
        pack_target_bytes=arguments.pack_target_bytes,
        progress_callback=progress_callback,
    )
    print(
        json.dumps(
            {
                "bundle_id": result.bundle_id,
                "manifest": str(result.manifest_path),
                "source_pack_count": result.source_pack_count,
                "pack_count": result.pack_count,
                "representation_count": result.representation_count,
                "pack_bytes": result.pack_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
