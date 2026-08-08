"""Spatial product-quality gate for Gaussian orthophotos and DSMs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TypedDict

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import Delaunay, QhullError


GAUSSIAN_MAP_COVERAGE_POLICY_ID = "GAUSSIAN_MAP_COVERAGE_V1"


@dataclass(frozen=True)
class SpatialCoveragePolicy:
    """Conservative acceptance thresholds for an aerial map product."""

    policy_id: str = GAUSSIAN_MAP_COVERAGE_POLICY_ID
    grid_size: int = 16
    minimum_valid_ratio: float = 0.50
    cell_coverage_threshold: float = 0.25
    minimum_covered_cells_ratio: float = 0.75
    minimum_worst_cell_ratio: float = 0.01
    minimum_camera_cell_ratio: float = 0.10
    minimum_footprint_fraction: float = 0.50

    def validate(self) -> None:
        if not 4 <= self.grid_size <= 64:
            raise ValueError("coverage grid_size must be between 4 and 64")
        for name, value in asdict(self).items():
            if name in {"policy_id", "grid_size"}:
                continue
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"coverage {name} must be in [0, 1]")


class CoverageCheck(TypedDict):
    name: str
    value: float
    minimum: float
    passed: bool


class SpatialCoverageReport(TypedDict):
    schema_version: int
    policy_id: str
    accepted: bool
    enforced: bool
    status: str
    footprint_source: str
    raster_shape: list[int]
    camera_count: int
    expected_cells: int
    valid_pixel_ratio: float
    covered_cells_ratio: float
    worst_cell_ratio: float
    p10_cell_ratio: float
    camera_cell_p10_ratio: float
    valid_bounds_pixels: list[int] | None
    expected_cell_indices: list[list[int]]
    cell_valid_ratios: list[dict[str, int | float]]
    checks: list[CoverageCheck]
    policy: dict[str, Any]


def _normalized_camera_points(
    camera_positions: NDArray[np.floating[Any]],
    extent: tuple[float, float, float, float],
) -> NDArray[np.float64]:
    positions = np.asarray(camera_positions, dtype=np.float64)
    if positions.ndim != 2 or positions.shape[1] < 2:
        raise ValueError("camera_positions must have shape (N, 2+)")
    x_min, x_max, y_min, y_max = (float(value) for value in extent)
    if not (x_max > x_min and y_max > y_min):
        raise ValueError("coverage extent must have positive width and height")
    points = positions[:, :2]
    points = points[np.isfinite(points).all(axis=1)]
    normalized = np.column_stack(
        (
            (points[:, 0] - x_min) / (x_max - x_min),
            (y_max - points[:, 1]) / (y_max - y_min),
        )
    )
    return np.unique(normalized, axis=0)


def _camera_cells(
    points: NDArray[np.float64],
    grid_size: int,
) -> set[tuple[int, int]]:
    inside = points[
        (points[:, 0] >= 0.0)
        & (points[:, 0] <= 1.0)
        & (points[:, 1] >= 0.0)
        & (points[:, 1] <= 1.0)
    ]
    cells: set[tuple[int, int]] = set()
    for x_value, y_value in inside:
        column = min(grid_size - 1, int(x_value * grid_size))
        row = min(grid_size - 1, int(y_value * grid_size))
        cells.add((row, column))
    return cells


def _footprint_cells(
    points: NDArray[np.float64],
    policy: SpatialCoveragePolicy,
) -> tuple[set[tuple[int, int]], str]:
    camera_cells = _camera_cells(points, policy.grid_size)
    if len(points) < 3:
        return camera_cells, "camera-cells"
    try:
        hull = Delaunay(points)
    except QhullError:
        return camera_cells, "camera-cells-collinear"

    expected: set[tuple[int, int]] = set()
    offsets = np.linspace(0.1, 0.9, 3, dtype=np.float64)
    for row in range(policy.grid_size):
        for column in range(policy.grid_size):
            samples = np.array(
                [
                    (
                        (column + x_offset) / policy.grid_size,
                        (row + y_offset) / policy.grid_size,
                    )
                    for y_offset in offsets
                    for x_offset in offsets
                ],
                dtype=np.float64,
            )
            footprint_fraction = float(np.mean(hull.find_simplex(samples) >= 0))
            if footprint_fraction >= policy.minimum_footprint_fraction:
                expected.add((row, column))
    expected.update(camera_cells)
    return expected, "camera-center-convex-hull"


def _cell_metrics(
    height: NDArray[np.floating[Any]],
    grid_size: int,
) -> tuple[
    dict[tuple[int, int], float],
    dict[tuple[int, int], tuple[int, int]],
    list[int] | None,
]:
    rows, columns = height.shape
    row_edges = np.linspace(0, rows, grid_size + 1, dtype=np.int64)
    column_edges = np.linspace(0, columns, grid_size + 1, dtype=np.int64)
    ratios: dict[tuple[int, int], float] = {}
    counts: dict[tuple[int, int], tuple[int, int]] = {}
    min_row, min_column = rows, columns
    max_row = max_column = -1

    for row in range(grid_size):
        row_start, row_end = int(row_edges[row]), int(row_edges[row + 1])
        for column in range(grid_size):
            column_start = int(column_edges[column])
            column_end = int(column_edges[column + 1])
            block = height[row_start:row_end, column_start:column_end]
            valid = np.isfinite(block)
            valid_count = int(np.count_nonzero(valid))
            counts[(row, column)] = (valid_count, int(valid.size))
            ratios[(row, column)] = float(valid_count / valid.size)
            row_any = np.flatnonzero(valid.any(axis=1))
            column_any = np.flatnonzero(valid.any(axis=0))
            if row_any.size:
                min_row = min(min_row, row_start + int(row_any[0]))
                max_row = max(max_row, row_start + int(row_any[-1]))
            if column_any.size:
                min_column = min(min_column, column_start + int(column_any[0]))
                max_column = max(max_column, column_start + int(column_any[-1]))

    bounds = (
        [min_column, min_row, max_column + 1, max_row + 1]
        if max_row >= 0 and max_column >= 0
        else None
    )
    return ratios, counts, bounds


def evaluate_spatial_coverage(
    height: NDArray[np.floating[Any]],
    *,
    extent: tuple[float, float, float, float],
    camera_positions: NDArray[np.floating[Any]],
    policy: SpatialCoveragePolicy | None = None,
    enforced: bool = True,
) -> SpatialCoverageReport:
    """Measure DSM validity inside a conservative projected flight footprint."""

    selected_policy = policy or SpatialCoveragePolicy()
    selected_policy.validate()
    height_array = np.asarray(height)
    if height_array.ndim != 2 or min(height_array.shape) < selected_policy.grid_size:
        raise ValueError("height raster must be 2D and at least as large as the coverage grid")

    points = _normalized_camera_points(camera_positions, extent)
    expected_cells, footprint_source = _footprint_cells(points, selected_policy)
    if not expected_cells:
        raise ValueError("no camera footprint intersects the rendered raster")
    cell_ratios, cell_counts, valid_bounds = _cell_metrics(
        height_array,
        selected_policy.grid_size,
    )
    expected_ratios = np.asarray(
        [cell_ratios[cell] for cell in sorted(expected_cells)],
        dtype=np.float64,
    )
    camera_cells = _camera_cells(points, selected_policy.grid_size)
    camera_ratios = np.asarray(
        [cell_ratios[cell] for cell in sorted(camera_cells)],
        dtype=np.float64,
    )
    expected_valid_pixels = sum(cell_counts[cell][0] for cell in expected_cells)
    expected_pixels = sum(cell_counts[cell][1] for cell in expected_cells)
    valid_ratio = float(expected_valid_pixels / expected_pixels)
    covered_cells_ratio = float(
        np.mean(expected_ratios >= selected_policy.cell_coverage_threshold)
    )
    worst_cell_ratio = float(np.min(expected_ratios))
    p10_cell_ratio = float(np.quantile(expected_ratios, 0.10))
    camera_cell_p10_ratio = (
        float(np.quantile(camera_ratios, 0.10))
        if camera_ratios.size
        else 0.0
    )
    checks: list[CoverageCheck] = [
        {
            "name": "valid_pixel_ratio",
            "value": valid_ratio,
            "minimum": selected_policy.minimum_valid_ratio,
            "passed": valid_ratio >= selected_policy.minimum_valid_ratio,
        },
        {
            "name": "covered_cells_ratio",
            "value": covered_cells_ratio,
            "minimum": selected_policy.minimum_covered_cells_ratio,
            "passed": covered_cells_ratio
            >= selected_policy.minimum_covered_cells_ratio,
        },
        {
            "name": "worst_cell_ratio",
            "value": worst_cell_ratio,
            "minimum": selected_policy.minimum_worst_cell_ratio,
            "passed": worst_cell_ratio >= selected_policy.minimum_worst_cell_ratio,
        },
        {
            "name": "camera_cell_p10_ratio",
            "value": camera_cell_p10_ratio,
            "minimum": selected_policy.minimum_camera_cell_ratio,
            "passed": camera_cell_p10_ratio
            >= selected_policy.minimum_camera_cell_ratio,
        },
    ]
    accepted = all(check["passed"] for check in checks)
    return {
        "schema_version": 1,
        "policy_id": selected_policy.policy_id,
        "accepted": accepted,
        "enforced": enforced,
        "status": (
            "accepted"
            if accepted
            else "rejected" if enforced else "measured-rejected"
        ),
        "footprint_source": footprint_source,
        "raster_shape": [int(height_array.shape[0]), int(height_array.shape[1])],
        "camera_count": len(points),
        "expected_cells": len(expected_cells),
        "valid_pixel_ratio": valid_ratio,
        "covered_cells_ratio": covered_cells_ratio,
        "worst_cell_ratio": worst_cell_ratio,
        "p10_cell_ratio": p10_cell_ratio,
        "camera_cell_p10_ratio": camera_cell_p10_ratio,
        "valid_bounds_pixels": valid_bounds,
        "expected_cell_indices": [list(cell) for cell in sorted(expected_cells)],
        "cell_valid_ratios": [
            {"row": row, "column": column, "valid_ratio": cell_ratios[(row, column)]}
            for row, column in sorted(expected_cells)
        ],
        "checks": checks,
        "policy": asdict(selected_policy),
    }
