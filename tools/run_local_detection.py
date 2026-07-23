"""Run tiling, YOLO OBB detection, deduplication, and GIS exports locally."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer


REPO_ROOT = Path(__file__).resolve().parents[1]
APP2_ROOT = REPO_ROOT / "app2-ia"
APP3_ROOT = REPO_ROOT / "app3-processing"
for import_path in (REPO_ROOT, APP2_ROOT, APP3_ROOT):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from detection_core import (  # noqa: E402
    normalize_yolo_model_variant,
    resolve_yolo_model_file,
    run_yolo_detection,
)
from processing_core import (  # noqa: E402
    dedupe_mission_detections,
    detections_to_geojson,
    geolocate_detection,
    render_annotated_orthomosaic,
    write_json,
    write_orthomosaic_tiles,
)


WORKSPACE_MARKER = ".droneai-local-workspace.json"


@dataclass(frozen=True)
class DetectionProfile:
    model_variant: str
    tile_size: int
    overlap: int
    confidence: float
    max_tiles: int | None


PROFILES = {
    "smoke": DetectionProfile(
        model_variant="yolo26n",
        tile_size=1024,
        overlap=256,
        confidence=0.20,
        max_tiles=1,
    ),
    "full": DetectionProfile(
        model_variant="yolo26l",
        tile_size=1024,
        overlap=256,
        confidence=0.20,
        max_tiles=None,
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("orthomosaic.low-memory.tif"),
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="full")
    parser.add_argument("--model-variant")
    parser.add_argument("--tile-size", type=int)
    parser.add_argument("--overlap", type=int)
    parser.add_argument("--confidence", type=float)
    parser.add_argument("--max-tiles", type=int)
    parser.add_argument(
        "--class",
        dest="classes",
        action="append",
        default=None,
        help="Requested semantic class; repeat for multiple classes.",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--keep-tiles", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def resolve_profile(args: argparse.Namespace) -> DetectionProfile:
    profile = PROFILES[args.profile]
    values = {
        "model_variant": args.model_variant,
        "tile_size": args.tile_size,
        "overlap": args.overlap,
        "confidence": args.confidence,
        "max_tiles": args.max_tiles,
    }
    resolved = replace(
        profile,
        **{key: value for key, value in values.items() if value is not None},
    )
    model_variant = normalize_yolo_model_variant(resolved.model_variant)
    resolved = replace(resolved, model_variant=model_variant)
    if resolved.tile_size < 256 or resolved.tile_size > 4096:
        raise ValueError("tile-size must be between 256 and 4096")
    if resolved.overlap < 0 or resolved.overlap >= resolved.tile_size:
        raise ValueError("overlap must be between 0 and tile-size - 1")
    if not 0 < resolved.confidence <= 1:
        raise ValueError("confidence must be in the interval (0, 1]")
    if resolved.max_tiles is not None and resolved.max_tiles <= 0:
        raise ValueError("max-tiles must be positive")
    return resolved


def ensure_inside_workspace(path: Path, workspace: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise ValueError(f"{label} must stay inside the marked workspace") from error
    return resolved


def resolve_paths(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path]:
    workspace = args.workspace.resolve()
    if not (workspace / WORKSPACE_MARKER).is_file():
        raise ValueError("workspace has no DroneAI local marker")

    source_candidate = (
        args.source if args.source.is_absolute() else workspace / args.source
    )
    source_path = ensure_inside_workspace(
        source_candidate,
        workspace,
        "source",
    )
    if not source_path.is_file():
        raise ValueError(f"source orthomosaic does not exist: {source_path}")

    output_candidate = (
        args.output_dir
        if args.output_dir and args.output_dir.is_absolute()
        else workspace
        / (args.output_dir or Path("detection_runs") / args.profile)
    )
    output_dir = ensure_inside_workspace(
        output_candidate,
        workspace,
        "output-dir",
    )
    return workspace, source_path, output_dir


def validate_orthomosaic(source_path: Path) -> dict:
    with rasterio.open(source_path) as src:
        if src.width <= 0 or src.height <= 0 or src.count < 1:
            raise ValueError("source orthomosaic is empty")
        if src.crs is None:
            raise ValueError("source orthomosaic has no CRS")
        if src.transform.is_identity:
            raise ValueError("source orthomosaic has no geospatial transform")
        return {
            "width": src.width,
            "height": src.height,
            "bands": src.count,
            "dtype": src.dtypes[0],
            "crs": src.crs.to_string(),
            "bounds": list(src.bounds),
            "transform": list(src.transform.to_gdal()),
        }


def prepare_output(output_dir: Path, force: bool) -> None:
    if output_dir.exists():
        if not force:
            raise ValueError(
                f"output directory already exists: {output_dir}; pass --force "
                "to replace only this profile output"
            )
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)


def translate_detection(
    detection: dict,
    tile: dict,
    transform,
    transformer: Transformer,
) -> dict:
    offset_x = float(tile["offset_x"])
    offset_y = float(tile["offset_y"])
    global_detection = {
        "tile_index": int(tile["tile_index"]),
        "global_pixel_x": float(detection["center_x"] + offset_x),
        "global_pixel_y": float(detection["center_y"] + offset_y),
        "confidence": round(float(detection["confidence"]), 6),
        "class_id": int(detection["class_id"]),
        "class_name": str(detection["class_name"]),
        "segment": [
            [float(point[0] + offset_x), float(point[1] + offset_y)]
            for point in detection["polygon"]
        ],
    }
    return geolocate_detection(global_detection, transform, transformer)


def class_counts(detections: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for detection in detections:
        label = str(detection.get("class_name", "unknown"))
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def main() -> int:
    args = parse_args()
    profile = resolve_profile(args)
    workspace, source_path, output_dir = resolve_paths(args)
    source_metadata = validate_orthomosaic(source_path)
    prepare_output(output_dir, args.force)

    report_path = output_dir / "detection_run.json"
    tiles_dir = output_dir / "tiles"
    started_at = time.time()
    report: dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "profile": args.profile,
        "parameters": asdict(profile),
        "classes": args.classes or ["car"],
        "workspace": str(workspace),
        "source": str(source_path),
        "source_metadata": source_metadata,
        "started_at": started_at,
    }
    write_json(report_path, report)

    try:
        print(
            f"[DETECT 5%] Tiling {source_path.name}: "
            f"{profile.tile_size}px, overlap={profile.overlap}px",
            flush=True,
        )
        tiles, tile_metadata = write_orthomosaic_tiles(
            source_path,
            tiles_dir,
            tile_size=profile.tile_size,
            overlap=profile.overlap,
            max_tiles=profile.max_tiles,
        )
        if not tiles:
            raise RuntimeError("tiling produced no tiles")

        _, model_path, checkpoint_name = resolve_yolo_model_file(
            profile.model_variant
        )
        report.update(
            tile_metadata=tile_metadata,
            model={
                "variant": profile.model_variant,
                "checkpoint": checkpoint_name,
                "path": str(model_path),
            },
        )
        write_json(report_path, report)

        with rasterio.open(source_path) as source:
            transform = source.transform
            transformer = Transformer.from_crs(
                source.crs,
                "EPSG:4326",
                always_xy=True,
            )

        raw_detections = []
        tile_results = []
        detection_started = time.perf_counter()
        for index, tile in enumerate(tiles, start=1):
            tile_started = time.perf_counter()
            detections, attempt = run_yolo_detection(
                tile["path"],
                args.classes or ["car"],
                profile.confidence,
                profile.model_variant,
            )
            translated = [
                translate_detection(
                    detection,
                    tile,
                    transform,
                    transformer,
                )
                for detection in detections
            ]
            raw_detections.extend(translated)
            tile_result = {
                "tile_index": tile["tile_index"],
                "offset_x": tile["offset_x"],
                "offset_y": tile["offset_y"],
                "detections": len(translated),
                "duration_seconds": time.perf_counter() - tile_started,
                "attempt": attempt,
            }
            tile_results.append(tile_result)
            progress = 10 + int(65 * index / len(tiles))
            print(
                f"[DETECT {progress}%] Tile {index}/{len(tiles)}: "
                f"{len(translated)} raw detections ({attempt['label']})",
                flush=True,
            )

        detection_seconds = time.perf_counter() - detection_started
        deduped = dedupe_mission_detections(raw_detections)
        geojson = detections_to_geojson(
            deduped,
            transform,
            source_metadata["crs"],
        )
        raw_path = output_dir / "detections.raw.json"
        detections_path = output_dir / "detections.json"
        geojson_path = output_dir / "detections.geojson"
        annotated_path = output_dir / "orthomosaic.annotated.tif"
        write_json(raw_path, raw_detections)
        write_json(detections_path, deduped)
        write_json(geojson_path, geojson)

        print(
            f"[DETECT 85%] Deduplicated {len(raw_detections)} -> "
            f"{len(deduped)} detections; rendering GeoTIFF",
            flush=True,
        )
        render_result = render_annotated_orthomosaic(
            source_path,
            annotated_path,
            deduped,
        )
        if not args.keep_tiles:
            shutil.rmtree(tiles_dir)

        confidences = [
            float(detection["confidence"])
            for detection in deduped
        ]
        finished_at = time.time()
        report.update(
            status="completed",
            finished_at=finished_at,
            duration_seconds=finished_at - started_at,
            detection_seconds=detection_seconds,
            tile_results=tile_results,
            result={
                "raw_detection_count": len(raw_detections),
                "deduplicated_detection_count": len(deduped),
                "class_counts": class_counts(deduped),
                "confidence_min": min(confidences) if confidences else None,
                "confidence_mean": (
                    float(np.mean(confidences)) if confidences else None
                ),
                "confidence_max": max(confidences) if confidences else None,
                "detections_file": str(detections_path),
                "geojson_file": str(geojson_path),
                "annotated_ortho_file": str(annotated_path),
                "render": render_result,
                "tiles_preserved": args.keep_tiles,
            },
        )
        write_json(report_path, report)
        print(
            f"[DETECT 100%] Done: {len(deduped)} detections, "
            f"{annotated_path}",
            flush=True,
        )
        print(json.dumps(report, indent=2), flush=True)
        return 0
    except Exception as error:
        finished_at = time.time()
        report.update(
            status="failed",
            finished_at=finished_at,
            duration_seconds=finished_at - started_at,
            error=f"{type(error).__name__}: {error}",
        )
        write_json(report_path, report)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
