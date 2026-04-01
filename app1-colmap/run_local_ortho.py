import argparse
import json
import os
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
if str(APP_DIR) not in sys.path:
    sys.path.append(str(APP_DIR))
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

import ortho_dsm


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

    return 0.05


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
    def report_fn(vol_id, step, progress, status="processing", log=None, details=None):
        message = log or ""
        print(f"[{step} {progress}% {status}] {message}")
        if details:
            print(json.dumps(details, indent=2, sort_keys=True))

    return report_fn


def parse_args():
    parser = argparse.ArgumentParser(description="Run the TrueOrtho builder locally from an existing mission workspace.")
    parser.add_argument(
        "--workspace",
        default="/mnt/j/workspace/vol_banyuls",
        help="Mission workspace containing dense/, alignment_transform.json, and geo_data.txt.crs.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output orthomosaic path. Defaults to <workspace>/orthomosaic.local.tif.",
    )
    parser.add_argument(
        "--resolution",
        type=float,
        default=None,
        help="Override orthomosaic resolution in meters per pixel.",
    )
    parser.add_argument(
        "--crs",
        default=None,
        help="Override output CRS. Defaults to value from geo_data.txt.crs.",
    )
    parser.add_argument(
        "--vol-id",
        default=None,
        help="Override volume id used in progress messages. Defaults to workspace folder name.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output from the local runner.",
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

    output_path = Path(args.output).resolve() if args.output else workspace_dir / "orthomosaic.local.tif"
    transform_path = workspace_dir / "alignment_transform.json"
    resolution = infer_resolution(workspace_dir, args.resolution)
    crs = infer_crs(workspace_dir, args.crs)
    vol_id = args.vol_id or workspace_dir.name

    report_fn = None if args.no_progress else make_reporter()

    print(f"Workspace: {workspace_dir}")
    print(f"Dense path: {dense_path}")
    print(f"Output: {output_path}")
    print(f"Resolution: {resolution}")
    print(f"CRS: {crs}")
    print(f"Transform: {transform_path if transform_path.exists() else 'none'}")

    ortho_dsm.generate_true_orthophoto_pytorch(
        dense_path=str(dense_path),
        ortho_file=str(output_path),
        utm_crs=crs,
        vol_id=vol_id,
        transform_file=str(transform_path) if transform_path.exists() else None,
        report_fn=report_fn,
        resolution=resolution,
    )

    print(f"Wrote orthomosaic: {output_path}")
    print(f"Diagnostics: {output_path}.diagnostics.json")


if __name__ == "__main__":
    main()
