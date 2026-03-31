#!/usr/bin/env python3
"""
CLI runner for Gaussian Splatting orthophoto generation.

Usage:
    python run_local_gaussian_ortho.py --workspace /mnt/j/workspace/vol_banyuls
    python run_local_gaussian_ortho.py --workspace /mnt/j/workspace/vol_banyuls --iterations 7000 --partition 2x2
"""
import argparse
import json
import os
import sys
from pathlib import Path

# Ensure the package is importable
ROOT_DIR = Path(__file__).resolve().parent
APP1_DIR = ROOT_DIR / "app1-colmap"
if str(APP1_DIR) not in sys.path:
    sys.path.insert(0, str(APP1_DIR))

from gaussian_ortho.generate_gaussian_orthophoto import generate_gaussian_orthophoto


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def infer_resolution(workspace_dir, explicit_resolution):
    if explicit_resolution is not None:
        return float(explicit_resolution)

    mission_state_path = workspace_dir / "mission_state.json"
    if mission_state_path.exists():
        try:
            mission_state = load_json(mission_state_path)
            value = mission_state.get("mission", {}).get("colmap_params", {}).get("ortho_mesh_resolution")
            if value is not None:
                return float(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    return 0.02  # Default GSD for Gaussian (finer than DSM-based)


def infer_crs(workspace_dir, explicit_crs):
    if explicit_crs:
        return explicit_crs

    crs_path = workspace_dir / "geo_data.txt.crs"
    if crs_path.exists():
        value = crs_path.read_text(encoding="utf-8").strip()
        if value:
            return value

    return "EPSG:4326"


def make_reporter():
    def report_fn(vol_id, step, progress, msg=None, details=None, **kwargs):
        message = msg or ""
        print(f"[{step} {progress}%] {message}")
        if details:
            print(json.dumps(details, indent=2, sort_keys=True))
    return report_fn


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a Gaussian Splatting orthophoto from a COLMAP workspace."
    )
    parser.add_argument(
        "--workspace",
        default="/mnt/j/workspace/vol_banyuls",
        help="Mission workspace containing dense/, alignment_transform.json, etc.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output GeoTIFF path. Defaults to <workspace>/orthomosaic.gaussian.tif.",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=None,
        help="Ground sample distance in metres per pixel.",
    )
    parser.add_argument(
        "--crs",
        default=None,
        help="Output CRS (e.g. EPSG:32631). Default: from geo_data.txt.crs.",
    )
    parser.add_argument(
        "--vol-id",
        default=None,
        help="Volume ID for progress messages.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=30000,
        help="Training iterations per cell (default: 30000).",
    )
    parser.add_argument(
        "--partition",
        default="1x1",
        help="Grid partition MxN (e.g. '2x2'). Default: 1x1 (no partition).",
    )
    parser.add_argument(
        "--sh-degree",
        type=int,
        default=3,
        help="Maximum SH degree (default: 3).",
    )
    parser.add_argument(
        "--no-fagk",
        action="store_true",
        help="Disable FAGK (view-dependent opacity).",
    )
    parser.add_argument(
        "--lambda-depth",
        type=float,
        default=0.0,
        help="Depth regularisation weight (default: 0.0, disabled).",
    )
    parser.add_argument(
        "--data-factor",
        type=int,
        default=4,
        help="Image downscaling factor for training (4=quarter-res, 1=full). Default: 4.",
    )
    parser.add_argument(
        "--strategy",
        default="mcmc",
        choices=["mcmc", "default"],
        help="Densification strategy: 'mcmc' (bounded, fast) or 'default'. Default: mcmc.",
    )
    parser.add_argument(
        "--cap-max",
        type=int,
        default=1_000_000,
        help="Max Gaussian count for mcmc strategy. Default: 1000000.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    workspace_dir = Path(args.workspace).resolve()
    if not workspace_dir.exists():
        raise FileNotFoundError(f"Workspace does not exist: {workspace_dir}")

    dense_path = workspace_dir / "dense"
    if not dense_path.is_dir():
        raise FileNotFoundError(f"Missing dense workspace: {dense_path}")

    output_path = Path(args.output).resolve() if args.output else workspace_dir / "orthomosaic.gaussian.tif"
    transform_path = workspace_dir / "alignment_transform.json"
    resolution = infer_resolution(workspace_dir, args.resolution)
    crs = infer_crs(workspace_dir, args.crs)
    vol_id = args.vol_id or workspace_dir.name

    # Parse partition
    partition_parts = args.partition.lower().split("x")
    partition_m = int(partition_parts[0])
    partition_n = int(partition_parts[1]) if len(partition_parts) > 1 else partition_m

    report_fn = None if args.no_progress else make_reporter()

    print(f"Workspace:   {workspace_dir}")
    print(f"Dense path:  {dense_path}")
    print(f"Output:      {output_path}")
    print(f"Resolution:  {resolution} m/px")
    print(f"CRS:         {crs}")
    print(f"Transform:   {transform_path if transform_path.exists() else 'none'}")
    print(f"Iterations:  {args.iterations}")
    print(f"Partition:   {partition_m}×{partition_n}")
    print(f"SH degree:   {args.sh_degree}")
    print(f"FAGK:        {not args.no_fagk}")
    print(f"Depth λ:     {args.lambda_depth}")
    print(f"Data factor: {args.data_factor}")
    print(f"Strategy:    {args.strategy} (cap_max={args.cap_max})")
    print()

    result = generate_gaussian_orthophoto(
        dense_path=str(dense_path),
        ortho_file=str(output_path),
        utm_crs=crs,
        vol_id=vol_id,
        transform_file=str(transform_path) if transform_path.exists() else None,
        report_fn=report_fn,
        resolution=resolution,
        iterations=args.iterations,
        partition_m=partition_m,
        partition_n=partition_n,
        sh_degree=args.sh_degree,
        fagk=not args.no_fagk,
        lambda_depth=args.lambda_depth,
        data_factor=args.data_factor,
        strategy=args.strategy,
        cap_max=args.cap_max,
    )

    print()
    print(f"Orthomosaic: {result['ortho_file']}")
    print(f"Height map:  {result['height_file']}")
    print(f"Checkpoint:  {result['final_ply']}")
    print(f"Dimensions:  {result['width']}×{result['height']} px")
    print(f"Gaussians:   {result['n_gaussians']}")


if __name__ == "__main__":
    main()
