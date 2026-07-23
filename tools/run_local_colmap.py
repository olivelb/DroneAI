"""Run a local COLMAP validation without Kafka, S3, Postgres, or Kubernetes.

This script is intended to run inside the repository's ``drone-colmap`` image.
Use ``tools/run_local_colmap.sh`` from WSL instead of invoking it directly.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
from pyproj import Transformer


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

WORKSPACE_MARKER = ".droneai-local-workspace.json"
GENERATED_PATHS = (
    "database.db",
    "geo_data.txt",
    "geo_data.txt.crs",
    "sparse",
    "sparse_geo",
    "dense",
    "sparse.ply",
    "sparse_geo.ply",
    "metrics.json",
    "model_analyzer.txt",
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--stage",
        choices=["preflight", "sparse", "align", "undistort", "all"],
        default="sparse",
    )
    parser.add_argument("--max-images", type=int, default=0, help="0 uses every image")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--selection",
        choices=["contiguous", "uniform"],
        default="contiguous",
    )
    parser.add_argument(
        "--matcher",
        choices=["spatial", "sequential", "exhaustive"],
        default="spatial",
    )
    parser.add_argument("--camera-model", default="OPENCV")
    parser.add_argument("--feature-max-image-size", type=int, default=3200)
    parser.add_argument("--feature-max-num-features", type=int, default=8192)
    parser.add_argument("--alignment-max-error", type=float, default=10.0)
    parser.add_argument("--gpu-index", type=int, default=0)
    parser.add_argument("--use-gpu", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--force", action="store_true", help="Rebuild generated workspace artifacts")
    return parser.parse_args()


def ensure_workspace(dataset: Path, workspace: Path) -> dict[str, Any]:
    dataset = dataset.resolve()
    workspace = workspace.resolve()
    if dataset == workspace or dataset in workspace.parents:
        raise ValueError("workspace must not be the dataset or one of its parent directories")
    if workspace in dataset.parents:
        raise ValueError("workspace must not contain the source dataset")

    workspace.mkdir(parents=True, exist_ok=True)
    marker_path = workspace / WORKSPACE_MARKER
    if marker_path.exists():
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if Path(marker["source_dataset"]).resolve() != dataset:
            raise ValueError(
                f"workspace already belongs to another dataset: {marker['source_dataset']}"
            )
        return marker

    preflight_outputs = {"dataset_preflight.json", "flight_path.geojson"}
    unexpected = [
        path
        for path in workspace.iterdir()
        if path.name not in preflight_outputs | {WORKSPACE_MARKER}
    ]
    if unexpected:
        raise ValueError(
            "workspace is not empty and has no DroneAI local marker; refusing to modify it"
        )
    marker = {"schema_version": 1, "source_dataset": str(dataset)}
    marker_path.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return marker


def reset_generated_artifacts(workspace: Path) -> None:
    if not (workspace / WORKSPACE_MARKER).is_file():
        raise ValueError("refusing to reset a workspace without the DroneAI marker")
    for relative_path in GENERATED_PATHS:
        target = workspace / relative_path
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()


def select_records(
    records: list[dict[str, Any]],
    *,
    maximum: int,
    start_index: int,
    strategy: str,
) -> list[dict[str, Any]]:
    readable = [record for record in records if record["readable"]]
    if start_index < 0 or start_index >= len(readable):
        raise ValueError(f"start-index must be between 0 and {max(0, len(readable) - 1)}")
    candidates = readable[start_index:]
    if maximum <= 0 or maximum >= len(candidates):
        return candidates
    if strategy == "contiguous":
        return candidates[:maximum]
    if maximum == 1:
        return [candidates[0]]
    indices = {
        round(index * (len(candidates) - 1) / (maximum - 1))
        for index in range(maximum)
    }
    return [candidates[index] for index in sorted(indices)]


def _selection_descriptor(record: dict[str, Any]) -> dict[str, Any]:
    return {"file": record["file"], "size_bytes": record["size_bytes"]}


def stage_images(
    dataset: Path,
    workspace: Path,
    selected_records: list[dict[str, Any]],
) -> bool:
    image_dir = workspace / "images"
    selection = [_selection_descriptor(record) for record in selected_records]
    selection_path = workspace / "selection.json"
    previous = None
    if selection_path.exists():
        previous = json.loads(selection_path.read_text(encoding="utf-8"))
    changed = previous != selection
    if changed:
        reset_generated_artifacts(workspace)
        if image_dir.exists():
            shutil.rmtree(image_dir)
        image_dir.mkdir(parents=True)
        for record in selected_records:
            source = dataset / record["file"]
            destination = image_dir / record["file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        selection_path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    return changed


def run_command(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    return subprocess.run(
        command,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def database_image_count(database_path: Path) -> int:
    if not database_path.exists():
        return 0
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM images").fetchone()
    return int(row[0]) if row else 0


def sparse_model_path(workspace: Path) -> Path:
    sparse_root = workspace / "sparse"
    candidates = sorted(
        path
        for path in sparse_root.iterdir()
        if path.is_dir() and (path / "cameras.bin").exists()
    ) if sparse_root.exists() else []
    if not candidates:
        raise RuntimeError("COLMAP did not produce a usable sparse model")
    return candidates[0]


def run_sparse(args: argparse.Namespace, selected_count: int) -> Path:
    workspace = args.workspace
    database_path = workspace / "database.db"
    image_dir = workspace / "images"
    sparse_root = workspace / "sparse"
    sparse_root.mkdir(exist_ok=True)
    use_gpu = "1" if args.use_gpu else "0"

    if database_image_count(database_path) != selected_count:
        run_command(
            [
                "colmap",
                "feature_extractor",
                "--database_path",
                str(database_path),
                "--image_path",
                str(image_dir),
                "--ImageReader.single_camera",
                "1",
                "--ImageReader.camera_model",
                args.camera_model,
                "--FeatureExtraction.type",
                "SIFT",
                "--FeatureExtraction.use_gpu",
                use_gpu,
                "--FeatureExtraction.gpu_index",
                str(args.gpu_index),
                "--FeatureExtraction.max_image_size",
                str(args.feature_max_image_size),
                "--SiftExtraction.max_num_features",
                str(args.feature_max_num_features),
            ]
        )

    existing_models = list(sparse_root.glob("*/cameras.bin"))
    if not existing_models:
        matcher_command = [
            "colmap",
            f"{args.matcher}_matcher",
            "--database_path",
            str(database_path),
            "--FeatureMatching.type",
            "SIFT_BRUTEFORCE",
            "--FeatureMatching.use_gpu",
            use_gpu,
            "--FeatureMatching.gpu_index",
            str(args.gpu_index),
        ]
        if args.matcher == "spatial":
            matcher_command += ["--SpatialMatching.ignore_z", "1"]
        run_command(matcher_command)

        mapper_command = [
            "colmap",
            "mapper",
            "--database_path",
            str(database_path),
            "--image_path",
            str(image_dir),
            "--output_path",
            str(sparse_root),
            "--Mapper.ba_use_gpu",
            use_gpu,
            "--Mapper.ba_gpu_index",
            str(args.gpu_index),
        ]
        try:
            run_command(mapper_command)
        except subprocess.CalledProcessError:
            if not args.use_gpu:
                raise
            print("GPU bundle adjustment failed; retrying the mapper with CPU BA.", flush=True)
            if sparse_root.exists():
                shutil.rmtree(sparse_root)
            sparse_root.mkdir()
            mapper_command[mapper_command.index("--Mapper.ba_use_gpu") + 1] = "0"
            run_command(mapper_command)
    return sparse_model_path(workspace)


def write_colmap_references(
    records: list[dict[str, Any]],
    workspace: Path,
    projected_crs: str,
) -> dict[str, tuple[float, float, float]]:
    transformer = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
    references: dict[str, tuple[float, float, float]] = {}
    lines = []
    for record in records:
        gps = record.get("gps")
        if not gps:
            continue
        x, y = transformer.transform(gps["longitude"], gps["latitude"])
        z = gps["altitude_m"] or 0.0
        references[record["file"]] = (x, y, z)
        lines.append(f"{record['file']} {x:.6f} {y:.6f} {z:.3f}")
    if len(references) < 3:
        raise RuntimeError("at least three selected images with GPS are required for alignment")
    (workspace / "geo_data.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (workspace / "geo_data.txt.crs").write_text(projected_crs + "\n", encoding="utf-8")
    return references


def run_alignment(
    sparse_model: Path,
    workspace: Path,
    references: dict[str, tuple[float, float, float]],
    maximum_error: float,
) -> Path:
    aligned_path = workspace / "sparse_geo"
    if not (aligned_path / "cameras.bin").exists():
        aligned_path.mkdir(exist_ok=True)
        run_command(
            [
                "colmap",
                "model_aligner",
                "--input_path",
                str(sparse_model),
                "--output_path",
                str(aligned_path),
                "--ref_images_path",
                str(workspace / "geo_data.txt"),
                "--ref_is_gps",
                "0",
                "--alignment_max_error",
                str(maximum_error),
            ]
        )
    return aligned_path


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def analyze_model(
    model_path: Path,
    *,
    selected_count: int,
    references: dict[str, tuple[float, float, float]] | None = None,
) -> dict[str, Any]:
    import pycolmap

    reconstruction = pycolmap.Reconstruction(str(model_path))
    point_errors = [
        float(point.error)
        for point in reconstruction.points3D.values()
        if math_is_finite(point.error)
    ]
    metrics: dict[str, Any] = {
        "model_path": str(model_path),
        "selected_images": selected_count,
        "registered_images": len(reconstruction.images),
        "registration_percent": round(100.0 * len(reconstruction.images) / selected_count, 2),
        "points3D": len(reconstruction.points3D),
        "mean_point_reprojection_error_px": mean(point_errors) if point_errors else None,
        "median_point_reprojection_error_px": median(point_errors) if point_errors else None,
    }
    if references:
        horizontal_errors = []
        vertical_errors = []
        euclidean_errors = []
        for image in reconstruction.images.values():
            reference = references.get(image.name)
            if reference is None:
                continue
            center = np.asarray(image.projection_center(), dtype=np.float64)
            delta = center - np.asarray(reference, dtype=np.float64)
            horizontal_errors.append(float(np.linalg.norm(delta[:2])))
            vertical_errors.append(abs(float(delta[2])))
            euclidean_errors.append(float(np.linalg.norm(delta)))
        metrics["gps_residuals_m"] = {
            "count": len(euclidean_errors),
            "horizontal_median": median(horizontal_errors) if horizontal_errors else None,
            "horizontal_p95": _percentile(horizontal_errors, 95),
            "vertical_median": median(vertical_errors) if vertical_errors else None,
            "euclidean_median": median(euclidean_errors) if euclidean_errors else None,
            "euclidean_p95": _percentile(euclidean_errors, 95),
            "euclidean_max": max(euclidean_errors) if euclidean_errors else None,
        }
    return metrics


def math_is_finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def run_undistortion(
    sparse_model: Path,
    workspace: Path,
    maximum_image_size: int,
) -> None:
    dense_path = workspace / "dense"
    if (dense_path / "sparse" / "cameras.bin").exists():
        return
    run_command(
        [
            "colmap",
            "image_undistorter",
            "--image_path",
            str(workspace / "images"),
            "--input_path",
            str(sparse_model),
            "--output_path",
            str(dense_path),
            "--max_image_size",
            str(maximum_image_size),
        ]
    )


def export_model(model_path: Path, output_path: Path) -> None:
    if output_path.exists():
        return
    run_command(
        [
            "colmap",
            "model_converter",
            "--input_path",
            str(model_path),
            "--output_path",
            str(output_path),
            "--output_type",
            "PLY",
        ]
    )


def main() -> int:
    args = parse_args()
    args.dataset = args.dataset.resolve()
    args.workspace = args.workspace.resolve()
    if args.max_images < 0:
        raise ValueError("max-images cannot be negative")
    if args.feature_max_image_size < 256:
        raise ValueError("feature-max-image-size must be at least 256")
    if args.alignment_max_error <= 0:
        raise ValueError("alignment-max-error must be positive")

    ensure_workspace(args.dataset, args.workspace)
    preflight_path = args.workspace / "dataset_preflight.json"
    if not preflight_path.is_file():
        raise RuntimeError(
            "dataset_preflight.json is missing; use tools/run_local_colmap.sh "
            "so the lightweight preflight container runs first"
        )
    report = json.loads(preflight_path.read_text(encoding="utf-8"))
    records = report["images"]

    selected = select_records(
        records,
        maximum=args.max_images,
        start_index=args.start_index,
        strategy=args.selection,
    )
    print(
        f"Selected {len(selected)}/{len(records)} images "
        f"({args.selection}, start index {args.start_index}).",
        flush=True,
    )
    if args.stage == "preflight":
        return 0

    selection_changed = stage_images(args.dataset, args.workspace, selected)
    if args.force and not selection_changed:
        reset_generated_artifacts(args.workspace)
    sparse_model = run_sparse(args, len(selected))
    metrics = analyze_model(sparse_model, selected_count=len(selected))
    references = None
    result_model = sparse_model

    if args.stage in {"align", "undistort", "all"}:
        projected_crs = report["summary"]["recommended_projected_crs"]
        if not projected_crs:
            raise RuntimeError("cannot align a dataset without a projected CRS")
        references = write_colmap_references(selected, args.workspace, projected_crs)
        aligned_model = run_alignment(
            sparse_model,
            args.workspace,
            references,
            args.alignment_max_error,
        )
        result_model = aligned_model
        metrics = analyze_model(
            aligned_model,
            selected_count=len(selected),
            references=references,
        )

    if args.stage in {"undistort", "all"}:
        run_undistortion(sparse_model, args.workspace, args.feature_max_image_size)

    export_model(
        result_model,
        args.workspace / ("sparse_geo.ply" if references else "sparse.ply"),
    )
    analyzer = run_command(
        ["colmap", "model_analyzer", "--path", str(sparse_model)],
        capture=True,
    )
    (args.workspace / "model_analyzer.txt").write_text(analyzer.stdout or "", encoding="utf-8")
    write_json(args.workspace / "metrics.json", metrics)
    print(json.dumps(metrics, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
