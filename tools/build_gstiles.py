#!/usr/bin/env python3
"""Build an immutable GSTile v1 bundle from a DroneGS binary PLY."""

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

from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_ply", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--leaf-size", type=int, default=65_536)
    parser.add_argument("--chunk-records", type=int, default=131_072)
    parser.add_argument("--temporary-root", type=Path)
    parser.add_argument("--crs")
    parser.add_argument("--origin", nargs=3, type=float, default=(0.0, 0.0, 0.0))
    parser.add_argument(
        "--progress-jsonl",
        action="store_true",
        help="emit structured build progress to stderr",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    progress_callback = None
    if arguments.progress_jsonl:
        progress_callback = lambda event: print(
            json.dumps(event, sort_keys=True), file=sys.stderr, flush=True
        )
    result = build_gstile_bundle(
        arguments.source_ply,
        arguments.output_directory,
        options=GsTileBuildOptions(
            leaf_size=arguments.leaf_size,
            chunk_records=arguments.chunk_records,
            temporary_root=arguments.temporary_root,
            coordinate_origin=tuple(arguments.origin),
            crs=arguments.crs,
            progress_callback=progress_callback,
        ),
    )
    print(
        json.dumps(
            {
                "bundle_id": result.bundle_id,
                "manifest": str(result.manifest_path),
                "gaussian_count": result.gaussian_count,
                "leaf_count": result.leaf_count,
                "pack_bytes": result.pack_bytes,
                "maximum_quantization_error": result.maximum_errors,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
