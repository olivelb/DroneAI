#!/usr/bin/env python3
"""Evaluate a georeferenced height raster at surveyed GCP coordinates."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any

import rasterio


def _summary(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "count": len(values),
        "mean": mean(values),
        "median": median(values),
        "rmse": math.sqrt(mean(value * value for value in values)),
        "p95": ordered[p95_index],
        "maximum": max(values),
    }


def evaluate_dsm(
    height_raster: str | Path,
    gcp_report: str | Path,
) -> dict[str, Any]:
    raster_path = Path(height_raster)
    report_path = Path(gcp_report)
    checkpoints = json.loads(report_path.read_text(encoding="utf-8"))
    model_crs = checkpoints.get("model_crs")
    points: list[dict[str, Any]] = []

    with rasterio.open(raster_path) as dataset:
        raster_crs = dataset.crs.to_string() if dataset.crs else None
        if model_crs and raster_crs != model_crs:
            raise ValueError(
                f"CRS mismatch: checkpoint model is {model_crs}, "
                f"height raster is {raster_crs or 'undefined'}"
            )

        for checkpoint in checkpoints.get("points", []):
            surveyed = checkpoint.get("surveyed_xyz")
            if checkpoint.get("status") != "ok" or not surveyed:
                continue
            x, y, surveyed_z = map(float, surveyed)
            row, column = dataset.index(x, y)
            result: dict[str, Any] = {
                "point_id": str(checkpoint.get("point_id")),
                "surveyed_xyz": [x, y, surveyed_z],
                "pixel_row": row,
                "pixel_column": column,
            }
            if not (0 <= row < dataset.height and 0 <= column < dataset.width):
                result["status"] = "outside-raster"
                points.append(result)
                continue
            raster_z = float(
                dataset.read(
                    1,
                    window=((row, row + 1), (column, column + 1)),
                )[0, 0]
            )
            if (
                not math.isfinite(raster_z)
                or dataset.nodata is not None
                and math.isfinite(dataset.nodata)
                and raster_z == dataset.nodata
            ):
                result["status"] = "nodata"
                points.append(result)
                continue
            signed_error = raster_z - surveyed_z
            result.update(
                {
                    "status": "ok",
                    "raster_z_m": raster_z,
                    "signed_error_m": signed_error,
                    "absolute_error_m": abs(signed_error),
                }
            )
            points.append(result)

        signed_errors = [
            point["signed_error_m"] for point in points if point["status"] == "ok"
        ]
        absolute_errors = [abs(error) for error in signed_errors]
        return {
            "schema_version": 1,
            "height_raster": str(raster_path),
            "gcp_report": str(report_path),
            "raster_crs": raster_crs,
            "pixel_size_m": [abs(dataset.transform.a), abs(dataset.transform.e)],
            "checkpoint_count": len(points),
            "successful_checkpoint_count": len(signed_errors),
            "signed_vertical_error_m": _summary(signed_errors),
            "absolute_vertical_error_m": _summary(absolute_errors),
            "points": points,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("height_raster", type=Path)
    parser.add_argument("gcp_report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate_dsm(args.height_raster, args.gcp_report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({key: report[key] for key in (
        "raster_crs",
        "pixel_size_m",
        "successful_checkpoint_count",
        "absolute_vertical_error_m",
    )}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
