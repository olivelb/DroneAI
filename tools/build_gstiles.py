#!/usr/bin/env python3
"""Build an immutable GSTile v1 bundle from a DroneGS binary PLY."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from gstile_cli_common import configure_repository_imports, jsonl_progress_callback

configure_repository_imports(__file__)

from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_ply", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--leaf-size", type=int, default=65_536)
    parser.add_argument(
        "--lod-proxy-size",
        type=int,
        help="opt into deterministic hierarchical LOD with this proxy size",
    )
    parser.add_argument(
        "--lod-proxy-strategy",
        choices=("adaptive-moment", "moment-matched", "spatial-stratified", "minhash"),
        default="moment-matched",
        help="LOD proxy strategy; adaptive-moment enables V4, replacement modes preserve legacy bundles",
    )
    parser.add_argument("--chunk-records", type=int, default=131_072)
    parser.add_argument("--pack-workers", type=int, choices=(1, 2, 4), default=1,
                        help="bounded parallel pack preparation; 1 keeps synchronous execution")
    parser.add_argument("--pack-pending-bytes", type=int, default=128 * 1024**2,
                        help="queued input/output reservation cap, excluding encoder scratch (default 128 MiB)")
    parser.add_argument(
        "--pack-target-bytes",
        type=int,
        help="aggregate spatially adjacent tiles into canonical packs up to this size",
    )
    parser.add_argument(
        "--filter-invisible-giant-scale",
        type=float,
        help=(
            "discard splats larger than this world-space scale only when their "
            "directional opacity is provably below the visibility threshold"
        ),
    )
    parser.add_argument(
        "--filter-visibility-opacity",
        type=float,
        default=0.05,
        help="directional opacity threshold used by the invisible-giant filter",
    )
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
    progress_callback = jsonl_progress_callback(arguments.progress_jsonl)
    result = build_gstile_bundle(
        arguments.source_ply,
        arguments.output_directory,
        options=GsTileBuildOptions(
            leaf_size=arguments.leaf_size,
            lod_proxy_size=arguments.lod_proxy_size,
            lod_proxy_strategy=arguments.lod_proxy_strategy,
            chunk_records=arguments.chunk_records,
            pack_target_bytes=arguments.pack_target_bytes,
            pack_workers=arguments.pack_workers,
            pack_pending_bytes=arguments.pack_pending_bytes,
            temporary_root=arguments.temporary_root,
            coordinate_origin=tuple(arguments.origin),
            crs=arguments.crs,
            progress_callback=progress_callback,
            invisible_gaussian_scale_threshold=arguments.filter_invisible_giant_scale,
            visibility_opacity_threshold=arguments.filter_visibility_opacity,
        ),
    )
    print(
        json.dumps(
            {
                "bundle_id": result.bundle_id,
                "manifest": str(result.manifest_path),
                "gaussian_count": result.gaussian_count,
                "input_gaussian_count": result.input_gaussian_count,
                "filtered_gaussian_count": result.filtered_gaussian_count,
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
