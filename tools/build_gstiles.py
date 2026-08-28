#!/usr/bin/env python3
"""Build an immutable GSTile v1 bundle from a DroneGS binary PLY."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from gstile_cli_common import configure_repository_imports, jsonl_progress_callback

configure_repository_imports(__file__)

from shared.gstile_defaults import (  # noqa: E402
    GSTILE_DEFAULTS_PROFILE,
    GSTILE_LOD_PROXY_SIZE,
    GSTILE_LOD_PROXY_STRATEGY,
    GSTILE_PACK_PENDING_BYTES,
    GSTILE_PACK_TARGET_BYTES,
    GSTILE_PACK_WORKERS,
    configure_gstile_process,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="The standalone CLI defaults OPENBLAS_THREAD_TIMEOUT to 16; explicit environment settings are preserved.",
    )
    parser.add_argument("source_ply", type=Path)
    parser.add_argument("output_directory", type=Path)
    parser.add_argument("--leaf-size", type=int, default=65_536)
    parser.add_argument(
        "--lod-proxy-size",
        type=int,
        default=GSTILE_LOD_PROXY_SIZE,
        help="hierarchical LOD proxy size (default: 16384); must not exceed leaf size",
    )
    parser.add_argument(
        "--lod-proxy-strategy",
        choices=("adaptive-moment",),
        default=GSTILE_LOD_PROXY_STRATEGY,
        help="production adaptive-moment V4 strategy",
    )
    parser.add_argument("--chunk-records", type=int, default=131_072)
    parser.add_argument("--pack-workers", type=int, choices=(1, 2, 4), default=GSTILE_PACK_WORKERS,
                        help="bounded parallel pack preparation (default: 2); 1 keeps synchronous execution")
    parser.add_argument("--pack-pending-bytes", type=int, default=GSTILE_PACK_PENDING_BYTES,
                        help="queued input/output reservation cap, excluding encoder scratch (default 128 MiB)")
    parser.add_argument(
        "--pack-target-bytes",
        type=int,
        default=GSTILE_PACK_TARGET_BYTES,
        help="depth-spatial aggregate target (default: 2097152 bytes)",
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
    from gaussian_tiles import GsTileBuildOptions, build_gstile_bundle

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
                "openblas_thread_timeout": os.environ.get("OPENBLAS_THREAD_TIMEOUT"),
                "build_configuration": {
                    "defaults_profile": GSTILE_DEFAULTS_PROFILE,
                    "lod_proxy_size": arguments.lod_proxy_size,
                    "lod_proxy_strategy": arguments.lod_proxy_strategy,
                    "pack_target_bytes": arguments.pack_target_bytes,
                    "pack_workers": arguments.pack_workers,
                    "pack_pending_bytes": arguments.pack_pending_bytes,
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    # Only the dedicated CLI process: never tune a host application's BLAS on import.
    # OpenBLAS reads this before NumPy initializes; 16 is a cycle exponent, not ms.
    # Keep thread counts and arithmetic unchanged, and respect even an explicit "0".
    configure_gstile_process()
    raise SystemExit(main())
