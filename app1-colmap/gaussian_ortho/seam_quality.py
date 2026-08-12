"""Evidence-only colour and height diagnostics along resident core seams."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np

from .partition import CellBounds


class CorePartition(Protocol):
    @property
    def bounds(self) -> CellBounds: ...


def _percentile(values: np.ndarray, percentile: float) -> float | None:
    finite = values[np.isfinite(values)]
    return float(np.percentile(finite, percentile)) if finite.size else None


def _metric(
    first: np.ndarray,
    second: np.ndarray,
    interior: Sequence[np.ndarray],
) -> dict[str, float | int | None]:
    differences = np.abs(first.astype(np.float64) - second.astype(np.float64))
    if differences.ndim == 2:
        differences = np.mean(differences, axis=1)
    finite = differences[np.isfinite(differences)]
    interior_values = np.concatenate(
        [value[np.isfinite(value)] for value in interior]
    ) if interior else np.empty(0, dtype=np.float64)
    mean = float(np.mean(finite)) if finite.size else None
    baseline = (
        float(np.mean(interior_values))
        if interior_values.size
        else None
    )
    ratio = (
        mean / max(baseline, 1.0e-9)
        if mean is not None and baseline is not None
        else None
    )
    return {
        "sample_count": int(finite.size),
        "mean": mean,
        "p95": _percentile(finite, 95.0),
        "interior_mean": baseline,
        "boundary_to_interior_ratio": ratio,
    }


def _differences(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    values = np.abs(first.astype(np.float64) - second.astype(np.float64))
    result: np.ndarray = (
        np.asarray(np.mean(values, axis=1), dtype=np.float64)
        if values.ndim == 2
        else values
    )
    return result


def _vertical_seam(
    rgb: np.ndarray,
    height: np.ndarray,
    *,
    column: int,
    row_start: int,
    row_end: int,
) -> dict[str, object] | None:
    if column < 1 or column >= rgb.shape[1] or row_end <= row_start:
        return None
    left_rgb = rgb[row_start:row_end, column - 1]
    right_rgb = rgb[row_start:row_end, column]
    left_height = height[row_start:row_end, column - 1]
    right_height = height[row_start:row_end, column]
    rgb_interior: list[np.ndarray] = []
    height_interior: list[np.ndarray] = []
    if column >= 2:
        rgb_interior.append(
            _differences(rgb[row_start:row_end, column - 2], left_rgb)
        )
        height_interior.append(
            _differences(height[row_start:row_end, column - 2], left_height)
        )
    if column + 1 < rgb.shape[1]:
        rgb_interior.append(
            _differences(right_rgb, rgb[row_start:row_end, column + 1])
        )
        height_interior.append(
            _differences(right_height, height[row_start:row_end, column + 1])
        )
    return {
        "orientation": "vertical",
        "pixel_index": column,
        "pixel_span": [row_start, row_end],
        "rgb_absolute_8bit": _metric(
            left_rgb,
            right_rgb,
            rgb_interior,
        ),
        "height_absolute": _metric(
            left_height,
            right_height,
            height_interior,
        ),
    }


def _horizontal_seam(
    rgb: np.ndarray,
    height: np.ndarray,
    *,
    row: int,
    column_start: int,
    column_end: int,
) -> dict[str, object] | None:
    if row < 1 or row >= rgb.shape[0] or column_end <= column_start:
        return None
    upper_rgb = rgb[row - 1, column_start:column_end]
    lower_rgb = rgb[row, column_start:column_end]
    upper_height = height[row - 1, column_start:column_end]
    lower_height = height[row, column_start:column_end]
    rgb_interior: list[np.ndarray] = []
    height_interior: list[np.ndarray] = []
    if row >= 2:
        rgb_interior.append(
            _differences(rgb[row - 2, column_start:column_end], upper_rgb)
        )
        height_interior.append(
            _differences(height[row - 2, column_start:column_end], upper_height)
        )
    if row + 1 < rgb.shape[0]:
        rgb_interior.append(
            _differences(lower_rgb, rgb[row + 1, column_start:column_end])
        )
        height_interior.append(
            _differences(lower_height, height[row + 1, column_start:column_end])
        )
    return {
        "orientation": "horizontal",
        "pixel_index": row,
        "pixel_span": [column_start, column_end],
        "rgb_absolute_8bit": _metric(
            upper_rgb,
            lower_rgb,
            rgb_interior,
        ),
        "height_absolute": _metric(
            upper_height,
            lower_height,
            height_interior,
        ),
    }


def evaluate_core_seams(
    rgb: np.ndarray,
    height: np.ndarray,
    *,
    extent: tuple[float, float, float, float],
    gsd: float,
    geo_origin: np.ndarray,
    partitions: Sequence[CorePartition],
) -> dict[str, Any]:
    """Measure stitched boundary jumps relative to nearby interior gradients."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or height.shape != rgb.shape[:2]:
        raise ValueError("seam evidence requires matching RGB and height rasters")
    if gsd <= 0 or not np.isfinite(gsd):
        raise ValueError("seam evidence requires a positive finite GSD")
    x_min, _x_max, _y_min, y_max = extent
    tolerance = max(1.0e-8, gsd * 1.0e-4)
    seams: list[dict[str, object]] = []
    for first_index, first in enumerate(partitions):
        for second_index in range(first_index + 1, len(partitions)):
            second = partitions[second_index]
            first_bounds = first.bounds
            second_bounds = second.bounds
            evidence: dict[str, object] | None = None
            boundary_x: float | None = None
            if abs(first_bounds.core_x_max - second_bounds.core_x_min) <= tolerance:
                boundary_x = first_bounds.core_x_max
            elif abs(second_bounds.core_x_max - first_bounds.core_x_min) <= tolerance:
                boundary_x = second_bounds.core_x_max
            if boundary_x is not None:
                overlap_min = max(first_bounds.core_y_min, second_bounds.core_y_min)
                overlap_max = min(first_bounds.core_y_max, second_bounds.core_y_max)
                column = round(
                    (boundary_x - float(geo_origin[0]) - x_min) / gsd
                )
                row_start = round(
                    (y_max - (overlap_max - float(geo_origin[1]))) / gsd
                )
                row_end = round(
                    (y_max - (overlap_min - float(geo_origin[1]))) / gsd
                )
                evidence = _vertical_seam(
                    rgb,
                    height,
                    column=column,
                    row_start=max(0, row_start),
                    row_end=min(rgb.shape[0], row_end),
                )
            boundary_y: float | None = None
            if evidence is None:
                if abs(first_bounds.core_y_max - second_bounds.core_y_min) <= tolerance:
                    boundary_y = first_bounds.core_y_max
                elif abs(second_bounds.core_y_max - first_bounds.core_y_min) <= tolerance:
                    boundary_y = second_bounds.core_y_max
            if boundary_y is not None:
                overlap_min = max(first_bounds.core_x_min, second_bounds.core_x_min)
                overlap_max = min(first_bounds.core_x_max, second_bounds.core_x_max)
                row = round(
                    (y_max - (boundary_y - float(geo_origin[1]))) / gsd
                )
                column_start = round(
                    (overlap_min - float(geo_origin[0]) - x_min) / gsd
                )
                column_end = round(
                    (overlap_max - float(geo_origin[0]) - x_min) / gsd
                )
                evidence = _horizontal_seam(
                    rgb,
                    height,
                    row=row,
                    column_start=max(0, column_start),
                    column_end=min(rgb.shape[1], column_end),
                )
            if evidence is not None:
                evidence["cores"] = [
                    [first_bounds.row, first_bounds.col],
                    [second_bounds.row, second_bounds.col],
                ]
                seams.append(evidence)
    return {
        "schema_version": 1,
        "qualification": "evidence-only",
        "seam_count": len(seams),
        "seams": seams,
    }
