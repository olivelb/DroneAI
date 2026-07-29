"""Run a local COLMAP validation without Kafka, S3, Postgres, or Kubernetes.

This script is intended to run inside the repository's ``drone-colmap`` image.
Use ``tools/run_local_colmap.sh`` from WSL instead of invoking it directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean, median
from typing import Any

import numpy as np
from pyproj import Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
APP1_DIR = REPO_ROOT / "app1-colmap"
if str(APP1_DIR) not in sys.path:
    sys.path.insert(0, str(APP1_DIR))

from alignment_support import (
    atomic_write_json,
    build_gps_pair_graph,
    build_mapping_command,
    caspar_compatibility,
    choose_auto_fallback,
    positioned_records_from_preflight,
    write_pair_list,
)

from shared.pipeline_params import PIPELINE_DEFAULTS
from shared.rtk_refinement import inject_database_pose_priors

WORKSPACE_MARKER = ".droneai-local-workspace.json"
MODERN_DEFAULTS = PIPELINE_DEFAULTS["modern"]
GENERATED_PATHS = (
    "database.db",
    "geo_data.txt",
    "geo_data.txt.crs",
    "geo_data.txt.crs.json",
    "sparse",
    "sparse_rtk",
    "sparse_geo",
    "dense",
    "sparse.ply",
    "sparse_geo.ply",
    "alignment_transform.json",
    "alignment_input.json",
    "undistortion_input.json",
    "metrics.json",
    "command_timings.json",
    "pairs.txt",
    "pair_graph.json",
    "rtk_prior_report.json",
    "model_analyzer.txt",
)

COMMAND_TIMINGS: list[dict[str, Any]] = []


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
        choices=["gps", "spatial", "sequential", "exhaustive"],
        default="gps",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "glomap", "caspar", "ceres"],
        default="auto",
    )
    parser.add_argument(
        "--feature-type",
        choices=["SIFT", "ALIKED_N16ROT", "ALIKED_N32"],
        default="SIFT",
    )
    parser.add_argument(
        "--matcher-type",
        choices=[
            "SIFT_BRUTEFORCE",
            "SIFT_LIGHTGLUE",
            "ALIKED_BRUTEFORCE",
            "ALIKED_LIGHTGLUE",
        ],
        default="SIFT_BRUTEFORCE",
    )
    parser.add_argument("--camera-model", default="SIMPLE_RADIAL")
    parser.add_argument(
        "--image-staging-mode",
        choices=["symlink", "copy"],
        default="copy",
        help=(
            "copy reads mounted Windows/network datasets in parallel and then runs COLMAP on the fast Linux filesystem"
        ),
    )
    parser.add_argument(
        "--image-copy-workers",
        type=int,
        default=8,
        help="Parallel copy workers used when image-staging-mode=copy.",
    )
    parser.add_argument(
        "--feature-max-image-size",
        type=int,
        default=int(MODERN_DEFAULTS["feature_max_image_size"]),
    )
    parser.add_argument(
        "--feature-max-num-features",
        type=int,
        default=int(MODERN_DEFAULTS["feature_max_num_features"]),
    )
    parser.add_argument("--gps-max-neighbors", type=int, default=32)
    parser.add_argument("--gps-min-neighbors", type=int, default=8)
    parser.add_argument("--gps-temporal-neighbors", type=int, default=6)
    parser.add_argument("--gps-max-distance-m", type=float, default=0.0)
    parser.add_argument("--minimum-registration-ratio", type=float, default=0.97)
    parser.add_argument(
        "--mapping-timeout-seconds",
        type=float,
        default=float(MODERN_DEFAULTS["mapping_timeout_seconds"]),
    )
    parser.add_argument("--global-max-tracks", type=int, default=2_000_000)
    parser.add_argument(
        "--global-ba-iterations",
        type=int,
        default=int(MODERN_DEFAULTS["global_mapper_ba_iterations"]),
    )
    parser.add_argument(
        "--global-ceres-iterations",
        type=int,
        default=int(MODERN_DEFAULTS["global_mapper_ceres_iterations"]),
    )
    parser.add_argument(
        "--global-retriangulation",
        action=argparse.BooleanOptionalAction,
        default=not bool(MODERN_DEFAULTS["global_mapper_skip_retriangulation"]),
        help="Enable GLOMAP's expensive final retriangulation/refinement pass.",
    )
    parser.add_argument(
        "--include-prefix",
        action="append",
        default=[],
        help="Only use records under this dataset-relative prefix; repeatable.",
    )
    parser.add_argument(
        "--gps-quality",
        choices=["unknown", "standard", "rtk"],
        default="standard",
        help="RTK automatically enables a bounded covariance-aware pose refinement.",
    )
    parser.add_argument(
        "--rtk-refinement",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Use DJI MRK positions and per-image covariance in pose_prior_mapper. "
            "The default is automatic: enabled for --gps-quality=rtk only."
        ),
    )
    parser.add_argument(
        "--rtk-refinement-timeout-seconds",
        type=float,
        default=900.0,
        help="Maximum runtime for the optional pose-prior Ceres refinement.",
    )
    parser.add_argument(
        "--rtk-refinement-iterations",
        type=int,
        default=25,
        help="Maximum Ceres iterations for the optional pose-prior global BA.",
    )
    parser.add_argument(
        "--projected-crs-mode",
        choices=["auto-local", "france-cc", "utm", "custom"],
        default="auto-local",
        help="Consumed by the preflight wrapper and recorded for reproducibility.",
    )
    parser.add_argument(
        "--projected-crs",
        default="",
        help="Explicit EPSG:<code> when projected-crs-mode=custom.",
    )
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
            raise ValueError(f"workspace already belongs to another dataset: {marker['source_dataset']}")
        return marker

    preflight_outputs = {"dataset_preflight.json", "flight_path.geojson"}
    unexpected = [path for path in workspace.iterdir() if path.name not in preflight_outputs | {WORKSPACE_MARKER}]
    if unexpected:
        raise ValueError("workspace is not empty and has no DroneAI local marker; refusing to modify it")
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
    indices = {round(index * (len(candidates) - 1) / (maximum - 1)) for index in range(maximum)}
    return [candidates[index] for index in sorted(indices)]


def _selection_descriptor(record: dict[str, Any], staging_mode: str) -> dict[str, Any]:
    return {
        "file": record["file"],
        "size_bytes": record["size_bytes"],
        "staging_mode": staging_mode,
    }


def stage_images(
    dataset: Path,
    workspace: Path,
    selected_records: list[dict[str, Any]],
    *,
    staging_mode: str = "copy",
    copy_workers: int = 8,
) -> bool:
    if staging_mode not in {"copy", "symlink"}:
        raise ValueError(f"Unsupported image staging mode: {staging_mode}")
    if copy_workers < 1:
        raise ValueError("copy_workers must be positive")
    image_dir = workspace / "images"
    selection = [_selection_descriptor(record, staging_mode) for record in selected_records]
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

        def stage_record(record: dict[str, Any]) -> None:
            source = dataset / record["file"]
            destination = image_dir / record["file"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if staging_mode == "symlink":
                destination.symlink_to(source)
            else:
                shutil.copyfile(source, destination)

        if staging_mode == "copy":
            with ThreadPoolExecutor(max_workers=copy_workers) as executor:
                list(executor.map(stage_record, selected_records))
        else:
            for record in selected_records:
                stage_record(record)
        selection_path.write_text(json.dumps(selection, indent=2) + "\n", encoding="utf-8")
    return changed


def run_command(
    command: list[str],
    *,
    capture: bool = False,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
            timeout=timeout_seconds,
        )
    except BaseException as error:
        COMMAND_TIMINGS.append(
            {
                "command": command,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
        )
        raise
    COMMAND_TIMINGS.append(
        {
            "command": command,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "status": "ok",
        }
    )
    return result


def database_image_count(database_path: Path) -> int:
    if not database_path.exists():
        return 0
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM images").fetchone()
    return int(row[0]) if row else 0


def registered_image_count(model_path: Path) -> int:
    images_path = model_path / "images.bin"
    try:
        with images_path.open("rb") as handle:
            encoded_count = handle.read(8)
    except OSError as error:
        raise RuntimeError(f"cannot read COLMAP image count from {images_path}") from error
    if len(encoded_count) != 8:
        raise RuntimeError(f"truncated COLMAP image count in {images_path}")
    return int(struct.unpack("<Q", encoded_count)[0])


def sparse_model_identity(model_path: Path) -> str:
    digest = hashlib.sha256()
    for filename in ("cameras.bin", "images.bin", "points3D.bin"):
        path = model_path / filename
        if not path.is_file():
            raise RuntimeError(f"incomplete sparse model: {path} is missing")
        digest.update(filename.encode("ascii"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def sparse_model_path(workspace: Path) -> Path:
    sparse_root = workspace / "sparse"
    candidates = (
        sorted(
            path
            for path in sparse_root.iterdir()
            if path.is_dir() and (path / "cameras.bin").is_file() and (path / "images.bin").is_file()
        )
        if sparse_root.exists()
        else []
    )
    if not candidates:
        raise RuntimeError("COLMAP did not produce a usable sparse model")
    return min(candidates, key=lambda path: (-registered_image_count(path), path.name))


def rtk_refinement_enabled(args: argparse.Namespace) -> bool:
    if args.rtk_refinement is not None:
        return bool(args.rtk_refinement)
    return args.gps_quality == "rtk"


def run_rtk_refinement(
    args: argparse.Namespace,
    sparse_model: Path,
) -> tuple[Path, dict[str, Any]]:
    output_path = args.workspace / "sparse_rtk"
    report_path = args.workspace / "rtk_prior_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    if (output_path / "cameras.bin").is_file():
        report["status"] = "completed"
        report["last_action"] = "reused"
        write_json(report_path, report)
        return output_path, report

    output_path.mkdir(exist_ok=True)
    command = [
        "colmap",
        "pose_prior_mapper",
        "--database_path",
        str(args.workspace / "database.db"),
        "--image_path",
        str(args.workspace / "images"),
        "--input_path",
        str(sparse_model),
        "--output_path",
        str(output_path),
        "--Mapper.ba_use_gpu",
        "1" if args.use_gpu else "0",
        "--Mapper.ba_gpu_index",
        str(args.gpu_index),
        "--Mapper.ba_local_backend",
        "CERES",
        "--Mapper.ba_global_backend",
        "CERES",
        "--Mapper.ba_local_max_num_iterations",
        str(args.rtk_refinement_iterations),
        "--Mapper.ba_global_max_num_iterations",
        str(args.rtk_refinement_iterations),
        "--Mapper.ba_local_max_refinements",
        "1",
        "--Mapper.ba_global_max_refinements",
        "1",
        "--Mapper.ba_global_ignore_redundant_points3D",
        "1",
        "--use_robust_loss_on_prior_position",
        "1",
        "--prior_position_loss_scale",
        "7.82",
    ]
    started_at = time.monotonic()
    try:
        run_command(command, timeout_seconds=args.rtk_refinement_timeout_seconds)
        if not (output_path / "cameras.bin").is_file():
            raise RuntimeError("pose_prior_mapper did not write a usable model")
        report.update(
            {
                "status": "completed",
                "last_action": "executed",
                "elapsed_seconds": time.monotonic() - started_at,
                "iterations": args.rtk_refinement_iterations,
                "timeout_seconds": args.rtk_refinement_timeout_seconds,
                "robust_loss": "cauchy",
                "robust_loss_scale": 7.82,
                "ba_backend": "CERES_GPU" if args.use_gpu else "CERES_CPU",
            }
        )
        write_json(report_path, report)
        for stale_path in (
            args.workspace / "sparse_geo",
            args.workspace / "dense",
        ):
            if stale_path.is_dir():
                shutil.rmtree(stale_path)
        for stale_path in (
            args.workspace / "alignment_transform.json",
            args.workspace / "alignment_input.json",
            args.workspace / "undistortion_input.json",
            args.workspace / "sparse.ply",
            args.workspace / "sparse_geo.ply",
        ):
            stale_path.unlink(missing_ok=True)
        return output_path, report
    except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        shutil.rmtree(output_path, ignore_errors=True)
        report.update(
            {
                "status": "fallback",
                "elapsed_seconds": time.monotonic() - started_at,
                "reason": str(error),
                "fallback_model": str(sparse_model),
            }
        )
        write_json(report_path, report)
        print(
            f"RTK refinement was not usable ({error}); keeping the verified GLOMAP/CASPAR model.",
            flush=True,
        )
        return sparse_model, report


# Keep failover and resume decisions in one ordered state machine: splitting them
# would make timeout-budget and artifact-cleanup invariants harder to verify.
def run_sparse(  # noqa: C901
    args: argparse.Namespace,
    selected_records: list[dict[str, Any]],
    projected_crs: str | None,
) -> Path:
    workspace = args.workspace
    database_path = workspace / "database.db"
    image_dir = workspace / "images"
    sparse_root = workspace / "sparse"
    sparse_root.mkdir(exist_ok=True)
    use_gpu = "1" if args.use_gpu else "0"
    selected_count = len(selected_records)
    model_dir = Path(os.getenv("COLMAP_MODEL_DIR", "/usr/local/share/colmap/models"))

    if database_image_count(database_path) != selected_count:
        feature_options = [
            "--FeatureExtraction.type",
            args.feature_type,
            "--FeatureExtraction.use_gpu",
            use_gpu,
            "--FeatureExtraction.gpu_index",
            str(args.gpu_index),
            "--FeatureExtraction.max_image_size",
            str(args.feature_max_image_size),
        ]
        if args.feature_type.startswith("ALIKED"):
            feature_options += [
                "--AlikedExtraction.max_num_features",
                str(args.feature_max_num_features),
            ]
            if args.feature_type == "ALIKED_N32":
                feature_options += [
                    "--AlikedExtraction.n32_model_path",
                    str(model_dir / "aliked-n32.onnx"),
                ]
            else:
                feature_options += [
                    "--AlikedExtraction.n16rot_model_path",
                    str(model_dir / "aliked-n16rot.onnx"),
                ]
        else:
            feature_options += [
                "--SiftExtraction.max_num_features",
                str(args.feature_max_num_features),
            ]
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
            ]
            + feature_options
        )

    if rtk_refinement_enabled(args) and not (workspace / "sparse_rtk" / "cameras.bin").is_file():
        prior_report = inject_database_pose_priors(database_path, selected_records)
        prior_report["status"] = "priors-injected"
        write_json(workspace / "rtk_prior_report.json", prior_report)

    existing_models = list(sparse_root.glob("*/cameras.bin"))
    if not existing_models:
        if args.matcher == "gps":
            if not projected_crs:
                raise RuntimeError("GPS matching requires a projected CRS")
            positioned = positioned_records_from_preflight(
                selected_records,
                projected_crs,
            )
            pairs, pair_stats = build_gps_pair_graph(
                positioned,
                max_neighbors=args.gps_max_neighbors,
                min_neighbors=args.gps_min_neighbors,
                temporal_neighbors=args.gps_temporal_neighbors,
                max_distance_m=args.gps_max_distance_m,
            )
            if not pairs:
                raise RuntimeError("GPS pair graph is empty")
            pair_path = workspace / "pairs.txt"
            write_pair_list(pair_path, pairs)
            atomic_write_json(workspace / "pair_graph.json", pair_stats)
            print(
                f"GPS graph: {pair_stats['pair_count']} pairs, mean degree {pair_stats['mean_degree']:.1f}.",
                flush=True,
            )
            matcher_command = [
                "colmap",
                "matches_importer",
                "--database_path",
                str(database_path),
                "--match_list_path",
                str(pair_path),
                "--match_type",
                "pairs",
                "--FeatureMatching.type",
                args.matcher_type,
                "--FeatureMatching.use_gpu",
                use_gpu,
                "--FeatureMatching.gpu_index",
                str(args.gpu_index),
            ]
        else:
            matcher_command = [
                "colmap",
                f"{args.matcher}_matcher",
                "--database_path",
                str(database_path),
                "--FeatureMatching.type",
                args.matcher_type,
                "--FeatureMatching.use_gpu",
                use_gpu,
                "--FeatureMatching.gpu_index",
                str(args.gpu_index),
            ]
            if args.matcher == "spatial":
                matcher_command += [
                    "--SpatialMatching.ignore_z",
                    "1",
                    "--SpatialMatching.max_num_neighbors",
                    str(args.gps_max_neighbors),
                    "--SpatialMatching.min_num_neighbors",
                    str(args.gps_min_neighbors),
                ]
        if args.matcher_type == "ALIKED_LIGHTGLUE":
            matcher_command += [
                "--AlikedMatching.lightglue_model_path",
                str(model_dir / "aliked-lightglue.onnx"),
            ]
        elif args.matcher_type == "SIFT_LIGHTGLUE":
            matcher_command += [
                "--SiftMatching.lightglue_model_path",
                str(model_dir / "sift-lightglue.onnx"),
            ]
        run_command(matcher_command)

        primary_engine = "glomap" if args.engine == "auto" else args.engine
        if primary_engine == "glomap":
            run_command(
                [
                    "colmap",
                    "view_graph_calibrator",
                    "--database_path",
                    str(database_path),
                ]
            )
        if not args.use_gpu and primary_engine in {"glomap", "caspar", "ceres"}:
            raise ValueError("the selected alignment engine requires --use-gpu")
        if primary_engine == "caspar":
            supported, models = caspar_compatibility(database_path)
            if not supported:
                raise RuntimeError(
                    f"Caspar requires PINHOLE or SIMPLE_RADIAL cameras; database contains {sorted(models)}"
                )
        mapper_command = build_mapping_command(
            primary_engine,
            database_path=database_path,
            image_path=image_dir,
            output_path=sparse_root,
            gpu_index=args.gpu_index,
            global_max_tracks=args.global_max_tracks,
            global_ba_iterations=args.global_ba_iterations,
            global_ceres_iterations=args.global_ceres_iterations,
            global_skip_retriangulation=not args.global_retriangulation,
        )
        mapping_started_at = time.monotonic()

        def remaining_mapping_budget() -> float:
            remaining = args.mapping_timeout_seconds - (time.monotonic() - mapping_started_at)
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    mapper_command,
                    args.mapping_timeout_seconds,
                )
            return remaining

        primary_error: BaseException | None = None
        try:
            run_command(
                mapper_command,
                timeout_seconds=remaining_mapping_budget(),
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            primary_error = error
            if args.engine != "auto":
                raise
            print(
                f"GLOMAP failed within its bounded budget ({error}); selecting an incremental GPU fallback.",
                flush=True,
            )

        primary_model = None
        if primary_error is None:
            try:
                primary_model = sparse_model_path(workspace)
            except RuntimeError:
                primary_model = None
        primary_registered = registered_image_count(primary_model) if primary_model is not None else 0
        minimum_registered = max(
            3,
            math.ceil(selected_count * args.minimum_registration_ratio),
        )
        if args.engine == "auto" and (primary_error is not None or primary_registered < minimum_registered):
            _, models = caspar_compatibility(database_path)
            fallback_engine = choose_auto_fallback(models)
            print(
                f"GLOMAP registered {primary_registered}/{selected_count}; "
                f"retrying the same verified matches with {fallback_engine.upper()}.",
                flush=True,
            )
            if sparse_root.exists():
                shutil.rmtree(sparse_root)
            sparse_root.mkdir()
            fallback_command = build_mapping_command(
                fallback_engine,
                database_path=database_path,
                image_path=image_dir,
                output_path=sparse_root,
                gpu_index=args.gpu_index,
                global_max_tracks=args.global_max_tracks,
                global_ba_iterations=args.global_ba_iterations,
                global_ceres_iterations=args.global_ceres_iterations,
                global_skip_retriangulation=not args.global_retriangulation,
            )
            run_command(
                fallback_command,
                timeout_seconds=remaining_mapping_budget(),
            )

    model = sparse_model_path(workspace)
    registered = registered_image_count(model)
    minimum_registered = max(
        3,
        math.ceil(selected_count * args.minimum_registration_ratio),
    )
    if registered < minimum_registered:
        raise RuntimeError(
            f"alignment quality gate failed: {registered}/{selected_count} images "
            f"registered, required {minimum_registered}; exhaustive matching and "
            "unbounded CPU BA are disabled"
        )
    if rtk_refinement_enabled(args):
        model, _ = run_rtk_refinement(args, model)
    return model


def write_colmap_references(
    records: list[dict[str, Any]],
    workspace: Path,
    projected_crs: str,
    projected_crs_selection: dict[str, Any] | None = None,
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
    positioned = [record for record in records if record.get("gps")]
    vertical_references = {str(record["gps"].get("vertical_reference", "unknown")) for record in positioned}
    vertical_errors = [
        float(record["gps"]["position_std_m"]["vertical_m"])
        for record in positioned
        if record["gps"].get("position_std_m") and record["gps"]["position_std_m"].get("vertical_m") is not None
    ]
    selection = projected_crs_selection or {}
    write_json(
        workspace / "geo_data.txt.crs.json",
        {
            "schema_version": 2,
            "projected_crs": projected_crs,
            "policy": selection.get("policy", "preflight"),
            "source": selection.get("source", "preflight"),
            "name": selection.get("name"),
            "vertical": {
                "reference": (next(iter(vertical_references)) if len(vertical_references) == 1 else "mixed-or-unknown"),
                "source": ("dji_mrk_ellh" if vertical_references == {"ellipsoidal"} else "exif_gps_altitude_or_mixed"),
                "uncertainty_m": (
                    {
                        "minimum": min(vertical_errors),
                        "maximum": max(vertical_errors),
                        "mean": mean(vertical_errors),
                        "median": median(vertical_errors),
                    }
                    if vertical_errors
                    else None
                ),
                "orthometric_conversion_applied": False,
            },
        },
    )
    return references


def run_alignment(
    sparse_model: Path,
    workspace: Path,
    references: dict[str, tuple[float, float, float]],
    maximum_error: float,
) -> Path:
    aligned_path = workspace / "sparse_geo"
    input_identity = sparse_model_identity(sparse_model)
    marker_path = workspace / "alignment_input.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    reusable = (
        (aligned_path / "cameras.bin").is_file()
        and marker.get("input_model_sha256") == input_identity
        and marker.get("maximum_error_m") == maximum_error
    )
    if not reusable:
        if aligned_path.is_dir():
            shutil.rmtree(aligned_path)
        (workspace / "alignment_transform.json").unlink(missing_ok=True)
        (workspace / "sparse_geo.ply").unlink(missing_ok=True)
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
        write_json(
            marker_path,
            {
                "schema_version": 1,
                "input_model": str(sparse_model),
                "input_model_sha256": input_identity,
                "maximum_error_m": maximum_error,
            },
        )
    return aligned_path


def write_alignment_transform(
    sparse_model: Path,
    aligned_model: Path,
    workspace: Path,
) -> Path:
    from shared.geo_alignment import (
        compute_reconstruction_alignment,
    )
    from shared.geo_alignment import (
        write_alignment_transform as write_transform,
    )

    transform_path = workspace / "alignment_transform.json"
    if transform_path.is_file():
        return transform_path
    transform = compute_reconstruction_alignment(sparse_model, aligned_model)
    return write_transform(transform_path, transform)


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
    point_errors = [float(point.error) for point in reconstruction.points3D.values() if math_is_finite(point.error)]
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
    marker_path = workspace / "undistortion_input.json"
    input_identity = sparse_model_identity(sparse_model)
    marker = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.is_file() else {}
    reusable = (
        (dense_path / "sparse" / "cameras.bin").is_file()
        and marker.get("input_model_sha256") == input_identity
        and marker.get("maximum_image_size") == maximum_image_size
    )
    if reusable:
        return
    if dense_path.is_dir():
        shutil.rmtree(dense_path)
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
    write_json(
        marker_path,
        {
            "schema_version": 1,
            "input_model": str(sparse_model),
            "input_model_sha256": input_identity,
            "maximum_image_size": maximum_image_size,
        },
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


def _validate_arguments(args: argparse.Namespace) -> None:
    checks = (
        (args.max_images < 0, "max-images cannot be negative"),
        (
            args.feature_max_image_size < 256,
            "feature-max-image-size must be at least 256",
        ),
        (args.image_copy_workers < 1, "image-copy-workers must be positive"),
        (args.alignment_max_error <= 0, "alignment-max-error must be positive"),
        (
            not 0 < args.minimum_registration_ratio <= 1,
            "minimum-registration-ratio must be in (0, 1]",
        ),
        (
            args.mapping_timeout_seconds <= 0,
            "mapping-timeout-seconds must be positive",
        ),
        (
            args.rtk_refinement_timeout_seconds <= 0,
            "rtk-refinement-timeout-seconds must be positive",
        ),
        (
            args.rtk_refinement_iterations < 1,
            "rtk-refinement-iterations must be positive",
        ),
        (
            not 1 <= args.gps_min_neighbors <= args.gps_max_neighbors,
            "gps neighbor bounds are inconsistent",
        ),
        (
            args.feature_type.startswith("ALIKED") != args.matcher_type.startswith("ALIKED"),
            "feature-type and matcher-type descriptor families must match",
        ),
    )
    for invalid, message in checks:
        if invalid:
            raise ValueError(message)


def _load_preflight_report(workspace: Path) -> dict[str, Any]:
    preflight_path = workspace / "dataset_preflight.json"
    if not preflight_path.is_file():
        raise RuntimeError(
            "dataset_preflight.json is missing; use tools/run_local_colmap.sh "
            "so the lightweight preflight container runs first"
        )
    return json.loads(preflight_path.read_text(encoding="utf-8"))


def _filter_records(
    records: list[dict[str, Any]],
    include_prefixes: list[str] | None,
) -> list[dict[str, Any]]:
    if not include_prefixes:
        return records
    prefixes = [prefix.strip().replace("\\", "/").strip("/") for prefix in include_prefixes if prefix.strip()]
    filtered = [
        record
        for record in records
        if any(record["file"] == prefix or record["file"].startswith(f"{prefix}/") for prefix in prefixes)
    ]
    if not filtered:
        raise ValueError(f"include-prefix filters selected no images: {include_prefixes}")
    return filtered


def _alignment_configuration(args: argparse.Namespace) -> dict[str, Any]:
    configuration = {
        "engine": args.engine,
        "matcher": args.matcher,
        "feature_type": args.feature_type,
        "matcher_type": args.matcher_type,
        "camera_model": args.camera_model,
        "minimum_registration_ratio": args.minimum_registration_ratio,
        "mapping_timeout_seconds": args.mapping_timeout_seconds,
        "rtk_refinement": rtk_refinement_enabled(args),
        "rtk_refinement_timeout_seconds": args.rtk_refinement_timeout_seconds,
        "rtk_refinement_iterations": args.rtk_refinement_iterations,
    }
    rtk_report_path = args.workspace / "rtk_prior_report.json"
    if rtk_report_path.is_file():
        configuration["rtk"] = json.loads(rtk_report_path.read_text(encoding="utf-8"))
    return configuration


def _finish_pipeline(
    args: argparse.Namespace,
    report: dict[str, Any],
    selected: list[dict[str, Any]],
    sparse_model: Path,
) -> None:
    projected_crs = report["summary"]["recommended_projected_crs"]
    metrics = analyze_model(sparse_model, selected_count=len(selected))
    references = None
    result_model = sparse_model

    if args.stage in {"align", "undistort", "all"}:
        if not projected_crs:
            raise RuntimeError("cannot align a dataset without a projected CRS")
        references = write_colmap_references(
            selected,
            args.workspace,
            projected_crs,
            report["summary"].get("projected_crs_selection"),
        )
        aligned_model = run_alignment(
            sparse_model,
            args.workspace,
            references,
            args.alignment_max_error,
        )
        write_alignment_transform(sparse_model, aligned_model, args.workspace)
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
    (args.workspace / "model_analyzer.txt").write_text(
        analyzer.stdout or "",
        encoding="utf-8",
    )
    metrics["alignment_configuration"] = _alignment_configuration(args)
    write_json(args.workspace / "metrics.json", metrics)
    write_json(
        args.workspace / "command_timings.json",
        {"commands": COMMAND_TIMINGS},
    )
    print(json.dumps(metrics, indent=2), flush=True)


def main() -> int:
    args = parse_args()
    args.dataset = args.dataset.resolve()
    args.workspace = args.workspace.resolve()
    _validate_arguments(args)
    ensure_workspace(args.dataset, args.workspace)

    report = _load_preflight_report(args.workspace)
    records = _filter_records(report["images"], args.include_prefix)
    selected = select_records(
        records,
        maximum=args.max_images,
        start_index=args.start_index,
        strategy=args.selection,
    )
    print(
        f"Selected {len(selected)}/{len(records)} images ({args.selection}, start index {args.start_index}).",
        flush=True,
    )
    if args.stage == "preflight":
        return 0

    selection_changed = stage_images(
        args.dataset,
        args.workspace,
        selected,
        staging_mode=args.image_staging_mode,
        copy_workers=args.image_copy_workers,
    )
    if args.force and not selection_changed:
        reset_generated_artifacts(args.workspace)
    projected_crs = report["summary"]["recommended_projected_crs"]
    sparse_model = run_sparse(args, selected, projected_crs)
    _finish_pipeline(args, report, selected, sparse_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
