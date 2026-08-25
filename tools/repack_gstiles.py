#!/usr/bin/env python3
"""Losslessly aggregate an immutable GSTile bundle into fewer packs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gstile_cli_common import configure_repository_imports, jsonl_progress_callback

configure_repository_imports(__file__)

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
    progress_callback = jsonl_progress_callback(arguments.progress_jsonl)
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
