"""Geographic core/buffer partitioning for resident Gaussian training blocks."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

import numpy as np

from .colmap_loader import PointCloud, Sim3Transform
from .camera_footprint import (
    GeographicSceneFrame,
    PlanarSceneFrame,
    camera_assignment_for_planar_buffer,
)
from .scene_info import SceneInfo, build_scene_info


type GroundLinear = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
]
type GroundOffset = tuple[float, float]


IDENTITY_GROUND_LINEAR: GroundLinear = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
)
ZERO_GROUND_OFFSET: GroundOffset = (0.0, 0.0)


@dataclass(frozen=True)
class CellBounds:
    """One projected-ground core and its expanded training buffer."""

    core_x_min: float
    core_x_max: float
    core_y_min: float
    core_y_max: float
    buffer_x_min: float
    buffer_x_max: float
    buffer_y_min: float
    buffer_y_max: float
    row: int
    col: int
    include_core_x_max: bool = False
    include_core_y_max: bool = False
    model_to_ground_linear: GroundLinear = IDENTITY_GROUND_LINEAR
    model_to_ground_offset: GroundOffset = ZERO_GROUND_OFFSET

    def as_dict(self) -> dict[str, object]:
        """Return a portable representation for cross-Job artifacts."""
        return {
            "core": [
                self.core_x_min,
                self.core_x_max,
                self.core_y_min,
                self.core_y_max,
            ],
            "buffer": [
                self.buffer_x_min,
                self.buffer_x_max,
                self.buffer_y_min,
                self.buffer_y_max,
            ],
            "row": self.row,
            "col": self.col,
            "include_core_x_max": self.include_core_x_max,
            "include_core_y_max": self.include_core_y_max,
            "model_to_ground_linear": [
                list(row) for row in self.model_to_ground_linear
            ],
            "model_to_ground_offset": list(self.model_to_ground_offset),
        }

    def project_model_points(self, points: Any, *, array_module: Any) -> Any:
        """Project model XYZ coordinates into the cell's ground XY frame."""
        linear = array_module.asarray(
            self.model_to_ground_linear,
            dtype=points.dtype,
        )
        offset = array_module.asarray(
            self.model_to_ground_offset,
            dtype=points.dtype,
        )
        return points @ linear.T + offset

    def core_mask(self, points: Any, *, array_module: Any) -> Any:
        ground = self.project_model_points(points, array_module=array_module)
        x_upper = (
            ground[:, 0] <= self.core_x_max
            if self.include_core_x_max
            else ground[:, 0] < self.core_x_max
        )
        y_upper = (
            ground[:, 1] <= self.core_y_max
            if self.include_core_y_max
            else ground[:, 1] < self.core_y_max
        )
        return (
            (ground[:, 0] >= self.core_x_min)
            & x_upper
            & (ground[:, 1] >= self.core_y_min)
            & y_upper
        )

    def buffer_mask(self, points: Any, *, array_module: Any) -> Any:
        ground = self.project_model_points(points, array_module=array_module)
        return (
            (ground[:, 0] >= self.buffer_x_min)
            & (ground[:, 0] <= self.buffer_x_max)
            & (ground[:, 1] >= self.buffer_y_min)
            & (ground[:, 1] <= self.buffer_y_max)
        )


def _finite_vector(
    payload: object,
    *,
    name: str,
    length: int,
) -> tuple[float, ...]:
    if (
        not isinstance(payload, list)
        or len(payload) != length
        or any(
            isinstance(value, bool) or not isinstance(value, (float, int))
            for value in payload
        )
    ):
        raise ValueError(f"Gaussian partition {name} is invalid")
    result = tuple(float(value) for value in payload)
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"Gaussian partition {name} must be finite")
    return result


def cell_bounds_from_dict(payload: object) -> CellBounds:
    """Validate and hydrate a projected-ground cell artifact."""
    if not isinstance(payload, dict):
        raise ValueError("Gaussian partition bounds are invalid")
    core = _finite_vector(payload.get("core"), name="core", length=4)
    buffer = _finite_vector(payload.get("buffer"), name="buffer", length=4)
    linear_raw = payload.get("model_to_ground_linear")
    if not isinstance(linear_raw, list) or len(linear_raw) != 2:
        raise ValueError("Gaussian partition ground transform is invalid")
    linear_rows = tuple(
        _finite_vector(row, name="ground transform", length=3)
        for row in linear_raw
    )
    offset = _finite_vector(
        payload.get("model_to_ground_offset"),
        name="ground offset",
        length=2,
    )
    row = payload.get("row")
    col = payload.get("col")
    include_x = payload.get("include_core_x_max")
    include_y = payload.get("include_core_y_max")
    if (
        isinstance(row, bool)
        or not isinstance(row, int)
        or row < 0
        or isinstance(col, bool)
        or not isinstance(col, int)
        or col < 0
        or not isinstance(include_x, bool)
        or not isinstance(include_y, bool)
    ):
        raise ValueError("Gaussian partition grid metadata is invalid")
    if not (
        core[0] < core[1]
        and core[2] < core[3]
        and buffer[0] <= core[0]
        and buffer[1] >= core[1]
        and buffer[2] <= core[2]
        and buffer[3] >= core[3]
    ):
        raise ValueError("Gaussian partition core/buffer ordering is invalid")
    return CellBounds(
        core_x_min=core[0],
        core_x_max=core[1],
        core_y_min=core[2],
        core_y_max=core[3],
        buffer_x_min=buffer[0],
        buffer_x_max=buffer[1],
        buffer_y_min=buffer[2],
        buffer_y_max=buffer[3],
        row=row,
        col=col,
        include_core_x_max=include_x,
        include_core_y_max=include_y,
        model_to_ground_linear=cast(GroundLinear, linear_rows),
        model_to_ground_offset=cast(GroundOffset, offset),
    )

def ground_projection_from_sim3(
    transform_data: Sim3Transform,
) -> tuple[GroundLinear, GroundOffset]:
    """Extract the projected XY affine map from a validated geographic Sim3."""
    rotation = np.asarray(transform_data.get("R"), dtype=np.float64)
    translation = np.asarray(transform_data.get("t"), dtype=np.float64)
    scale = float(transform_data.get("scale", 0.0))
    if rotation.shape != (3, 3) or translation.shape != (3,):
        raise ValueError("geographic partitioning requires a 3D Sim3 transform")
    if (
        not np.isfinite(rotation).all()
        or not np.isfinite(translation).all()
        or not math.isfinite(scale)
        or scale <= 0.0
    ):
        raise ValueError("geographic partitioning requires a finite positive Sim3")
    linear_array = scale * rotation[:2, :]
    linear: GroundLinear = (
        (
            float(linear_array[0, 0]),
            float(linear_array[0, 1]),
            float(linear_array[0, 2]),
        ),
        (
            float(linear_array[1, 0]),
            float(linear_array[1, 1]),
            float(linear_array[1, 2]),
        ),
    )
    offset: GroundOffset = (float(translation[0]), float(translation[1]))
    return linear, offset


def _project_points(
    points: np.ndarray,
    linear: GroundLinear,
    offset: GroundOffset,
) -> np.ndarray:
    xyz = np.asarray(points, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or xyz.shape[0] < 1:
        raise ValueError("geographic partitioning requires model points shaped (N, 3)")
    if not np.isfinite(xyz).all():
        raise ValueError("geographic partition points must be finite")
    projected: np.ndarray = (
        xyz @ np.asarray(linear, dtype=np.float64).T
        + np.asarray(offset, dtype=np.float64)
    )
    return projected


def _ground_extent(
    ground_xy: np.ndarray,
    *,
    quantile: float,
) -> tuple[float, float, float, float]:
    if not 0.0 <= quantile < 0.5:
        raise ValueError("partition extent quantile must be in [0, 0.5)")
    effective_quantile = quantile if ground_xy.shape[0] >= 200 else 0.0
    lower = np.quantile(ground_xy, effective_quantile, axis=0)
    upper = np.quantile(ground_xy, 1.0 - effective_quantile, axis=0)
    if not np.all(upper > lower):
        raise ValueError("geographic partition ground extent is degenerate")
    return float(lower[0]), float(upper[0]), float(lower[1]), float(upper[1])


def compute_partition_grid(
    scene: SceneInfo,
    m: int = 2,
    n: int = 2,
    overlap: float = 0.20,
    *,
    model_to_ground_linear: GroundLinear = IDENTITY_GROUND_LINEAR,
    model_to_ground_offset: GroundOffset = ZERO_GROUND_OFFSET,
    extent_quantile: float = 0.005,
) -> list[CellBounds]:
    """Create an explicit core/buffer grid in projected ground coordinates."""
    if m < 1 or n < 1:
        raise ValueError("partition grid dimensions must be positive")
    if not math.isfinite(overlap) or not 0.0 <= overlap < 1.0:
        raise ValueError("partition overlap must be in [0, 1)")
    ground_xy = _project_points(
        scene.point_cloud.points,
        model_to_ground_linear,
        model_to_ground_offset,
    )
    x_lo, x_hi, y_lo, y_hi = _ground_extent(
        ground_xy,
        quantile=extent_quantile,
    )
    width = (x_hi - x_lo) / n
    height = (y_hi - y_lo) / m
    pad_x = width * overlap
    pad_y = height * overlap

    cells: list[CellBounds] = []
    for row in range(m):
        for col in range(n):
            core_x_min = x_lo + col * width
            core_x_max = x_lo + (col + 1) * width
            core_y_min = y_lo + row * height
            core_y_max = y_lo + (row + 1) * height
            cells.append(
                CellBounds(
                    core_x_min=core_x_min,
                    core_x_max=core_x_max,
                    core_y_min=core_y_min,
                    core_y_max=core_y_max,
                    buffer_x_min=core_x_min - pad_x,
                    buffer_x_max=core_x_max + pad_x,
                    buffer_y_min=core_y_min - pad_y,
                    buffer_y_max=core_y_max + pad_y,
                    row=row,
                    col=col,
                    include_core_x_max=col == n - 1,
                    include_core_y_max=row == m - 1,
                    model_to_ground_linear=model_to_ground_linear,
                    model_to_ground_offset=model_to_ground_offset,
                )
            )
    return cells


def plan_partition_grid(
    scene: SceneInfo,
    required_cell_count: int,
    *,
    model_to_ground_linear: GroundLinear = IDENTITY_GROUND_LINEAR,
    model_to_ground_offset: GroundOffset = ZERO_GROUND_OFFSET,
    extent_quantile: float = 0.005,
) -> tuple[int, int]:
    """Choose rows/columns that keep projected resident blocks compact."""
    if required_cell_count < 1:
        raise ValueError("required partition cell count must be positive")
    if required_cell_count == 1:
        return 1, 1
    ground_xy = _project_points(
        scene.point_cloud.points,
        model_to_ground_linear,
        model_to_ground_offset,
    )
    x_min, x_max, y_min, y_max = _ground_extent(
        ground_xy,
        quantile=extent_quantile,
    )
    scene_aspect = (x_max - x_min) / (y_max - y_min)
    best: tuple[float, int, int, int] | None = None
    maximum_axis = max(2, required_cell_count)
    for rows in range(1, maximum_axis + 1):
        columns = math.ceil(required_cell_count / rows)
        cell_count = rows * columns
        cell_aspect = scene_aspect * rows / columns
        shape_penalty = abs(math.log(max(cell_aspect, 1.0e-12)))
        overprovision_penalty = (
            2.0 * (cell_count - required_cell_count) / required_cell_count
        )
        candidate = (
            shape_penalty + overprovision_penalty,
            cell_count,
            rows,
            columns,
        )
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise RuntimeError("unable to plan a geographic partition grid")
    return best[2], best[3]


def _filter_points_in_buffer(point_cloud: PointCloud, cell: CellBounds) -> PointCloud:
    mask = cell.buffer_mask(point_cloud.points, array_module=np)
    return PointCloud(
        points=point_cloud.points[mask],
        colors=point_cloud.colors[mask],
        normals=point_cloud.normals[mask],
    )


def partition_scene(
    scene: SceneInfo,
    m: int = 2,
    n: int = 2,
    overlap: float = 0.20,
    min_cameras: int = 5,
    *,
    model_to_ground_linear: GroundLinear = IDENTITY_GROUND_LINEAR,
    model_to_ground_offset: GroundOffset = ZERO_GROUND_OFFSET,
    geographic_frame: GeographicSceneFrame | None = None,
    planar_frame: PlanarSceneFrame | None = None,
    crop_margin_pixels: int = 128,
    maximum_view_incidence_degrees: float = 75.0,
    minimum_plane_overlap_m2: float = 1.0,
) -> list[tuple[CellBounds, SceneInfo]]:
    """Build resident scenes from footprint-visible native-image crops."""
    if min_cameras < 1:
        raise ValueError("minimum partition camera count must be positive")
    selected_frame = planar_frame or geographic_frame
    if selected_frame is None:
        raise ValueError(
            "partition camera selection requires a planar scene frame"
        )
    cells = compute_partition_grid(
        scene,
        m,
        n,
        overlap,
        model_to_ground_linear=model_to_ground_linear,
        model_to_ground_offset=model_to_ground_offset,
    )
    result: list[tuple[CellBounds, SceneInfo]] = []
    for cell in cells:
        assignments = [
            (camera, assignment)
            for camera in scene.train_cameras
            if (
                assignment := camera_assignment_for_planar_buffer(
                    camera,
                    selected_frame,
                    (
                        cell.buffer_x_min,
                        cell.buffer_x_max,
                        cell.buffer_y_min,
                        cell.buffer_y_max,
                    ),
                    crop_margin_pixels=crop_margin_pixels,
                    maximum_nadir_incidence_degrees=(
                        maximum_view_incidence_degrees
                    ),
                    minimum_ground_overlap_m2=minimum_plane_overlap_m2,
                )
            )
            is not None
        ]
        cameras = [camera for camera, _assignment in assignments]
        if len(cameras) < min_cameras:
            continue
        point_cloud = _filter_points_in_buffer(scene.point_cloud, cell)
        if point_cloud.points.shape[0] < 100:
            continue
        result.append(
            (
                cell,
                build_scene_info(
                    train_cameras=cameras,
                    test_cameras=[],
                    point_cloud=point_cloud,
                    dense_path=scene.dense_path,
                    images_dir=scene.images_dir,
                    image_crops={
                        camera.image_name: assignment.crop
                        for camera, assignment in assignments
                    },
                ),
            )
        )
    return result
