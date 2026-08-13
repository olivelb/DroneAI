"""Calibrated ground-footprint visibility and native source-image crops."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .colmap_loader import CameraInfo, Sim3Transform


@dataclass(frozen=True)
class NativeImageCrop:
    """A right/bottom-exclusive crop in untouched source-image pixels."""

    source_x: int
    source_y: int
    width: int
    height: int
    source_width: int
    source_height: int

    def __post_init__(self) -> None:
        if min(
            self.source_x,
            self.source_y,
            self.width,
            self.height,
            self.source_width,
            self.source_height,
        ) < 0:
            raise ValueError("native image crop values must be non-negative")
        if self.width < 1 or self.height < 1:
            raise ValueError("native image crop dimensions must be positive")
        if (
            self.source_x + self.width > self.source_width
            or self.source_y + self.height > self.source_height
        ):
            raise ValueError("native image crop exceeds its source image")


@dataclass(frozen=True)
class PlanarSceneFrame:
    """Metric product-plane transform plus a robust depth envelope."""

    rotation: np.ndarray
    scale: float
    translation: np.ndarray
    terrain_z_min: float
    terrain_z_max: float

    @property
    def ground_linear(self) -> tuple[
        tuple[float, float, float],
        tuple[float, float, float],
    ]:
        linear = self.scale * self.rotation[:2, :]
        return (
            (float(linear[0, 0]), float(linear[0, 1]), float(linear[0, 2])),
            (float(linear[1, 0]), float(linear[1, 1]), float(linear[1, 2])),
        )

    @property
    def ground_offset(self) -> tuple[float, float]:
        return float(self.translation[0]), float(self.translation[1])


@dataclass(frozen=True)
class GeographicSceneFrame(PlanarSceneFrame):
    """Projected-ground Sim3 frame."""


@dataclass(frozen=True)
class FacadeSceneFrame(PlanarSceneFrame):
    """Local metric wall frame."""


@dataclass(frozen=True)
class CameraBlockAssignment:
    """Evidence that one calibrated image sees a block training buffer."""

    crop: NativeImageCrop
    maximum_ground_overlap_m2: float
    nadir_incidence_degrees: float


def geographic_scene_frame(
    model_points: np.ndarray,
    transform_data: Sim3Transform,
    *,
    terrain_quantile: float = 0.005,
    terrain_margin_m: float = 2.0,
) -> GeographicSceneFrame:
    """Build the projected scene frame and robust vertical envelope."""
    rotation = np.asarray(transform_data.get("R"), dtype=np.float64)
    translation = np.asarray(transform_data.get("t"), dtype=np.float64)
    scale = float(transform_data.get("scale", 0.0))
    points = np.asarray(model_points, dtype=np.float64)
    if rotation.shape != (3, 3) or translation.shape != (3,):
        raise ValueError("camera footprint selection requires a 3D Sim3")
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] < 3:
        raise ValueError("camera footprint selection requires 3D terrain points")
    if (
        not np.isfinite(rotation).all()
        or not np.isfinite(translation).all()
        or not np.isfinite(points).all()
        or not math.isfinite(scale)
        or scale <= 0.0
    ):
        raise ValueError("camera footprint Sim3 and points must be finite")
    if not 0.0 <= terrain_quantile < 0.5:
        raise ValueError("terrain height quantile must be in [0, 0.5)")
    if terrain_margin_m < 0.0 or not math.isfinite(terrain_margin_m):
        raise ValueError("terrain height margin must be finite and non-negative")
    geographic = (scale * (rotation @ points.T)).T + translation
    effective_quantile = terrain_quantile if len(geographic) >= 200 else 0.0
    terrain_z_min = float(np.quantile(geographic[:, 2], effective_quantile))
    terrain_z_max = float(
        np.quantile(geographic[:, 2], 1.0 - effective_quantile)
    )
    return GeographicSceneFrame(
        rotation=rotation,
        scale=scale,
        translation=translation,
        terrain_z_min=terrain_z_min - terrain_margin_m,
        terrain_z_max=terrain_z_max + terrain_margin_m,
    )


def facade_scene_frame(
    model_points: np.ndarray,
    *,
    origin: np.ndarray,
    world_to_facade: np.ndarray,
    meters_per_model_unit: float,
    depth_quantile: float = 0.005,
    depth_margin_m: float = 0.25,
) -> FacadeSceneFrame:
    """Build a metric planar frame used for facade visibility and crops."""

    points = np.asarray(model_points, dtype=np.float64)
    frame_origin = np.asarray(origin, dtype=np.float64)
    rotation = np.asarray(world_to_facade, dtype=np.float64)
    scale = float(meters_per_model_unit)
    if points.ndim != 2 or points.shape[1] != 3 or points.shape[0] < 3:
        raise ValueError("facade footprint selection requires 3D model points")
    if frame_origin.shape != (3,) or rotation.shape != (3, 3):
        raise ValueError("facade footprint selection requires a 3D frame")
    if (
        not np.isfinite(points).all()
        or not np.isfinite(frame_origin).all()
        or not np.isfinite(rotation).all()
        or not math.isfinite(scale)
        or scale <= 0.0
    ):
        raise ValueError("facade footprint frame and points must be finite")
    if not 0.0 <= depth_quantile < 0.5:
        raise ValueError("facade depth quantile must be in [0, 0.5)")
    if depth_margin_m < 0.0 or not math.isfinite(depth_margin_m):
        raise ValueError("facade depth margin must be finite and non-negative")
    translation = -scale * (rotation @ frame_origin)
    facade = (scale * (rotation @ points.T)).T + translation
    effective_quantile = depth_quantile if len(facade) >= 200 else 0.0
    depth_min = float(np.quantile(facade[:, 2], effective_quantile))
    depth_max = float(np.quantile(facade[:, 2], 1.0 - effective_quantile))
    return FacadeSceneFrame(
        rotation=rotation,
        scale=scale,
        translation=translation,
        terrain_z_min=depth_min - depth_margin_m,
        terrain_z_max=depth_max + depth_margin_m,
    )


def _camera_pose(
    camera: CameraInfo,
    frame: PlanarSceneFrame,
) -> tuple[np.ndarray, np.ndarray]:
    center = (
        frame.scale * frame.rotation @ np.asarray(camera.T, dtype=np.float64)
        + frame.translation
    )
    camera_to_geographic = frame.rotation @ np.asarray(camera.R, dtype=np.float64)
    return center, camera_to_geographic


def _footprint_at_height(
    camera: CameraInfo,
    center: np.ndarray,
    camera_to_geographic: np.ndarray,
    height: float,
) -> np.ndarray | None:
    pixels = (
        (0.0, 0.0),
        (float(camera.width), 0.0),
        (float(camera.width), float(camera.height)),
        (0.0, float(camera.height)),
    )
    footprint: list[tuple[float, float]] = []
    for pixel_x, pixel_y in pixels:
        ray_camera = np.array(
            [
                (pixel_x - camera.cx) / camera.fx,
                (pixel_y - camera.cy) / camera.fy,
                1.0,
            ],
            dtype=np.float64,
        )
        ray = camera_to_geographic @ ray_camera
        if ray[2] >= -1.0e-9:
            return None
        distance = (height - center[2]) / ray[2]
        if distance <= 0.0 or not math.isfinite(float(distance)):
            return None
        point = center + distance * ray
        footprint.append((float(point[0]), float(point[1])))
    result: np.ndarray = np.asarray(footprint, dtype=np.float64)
    return result


def _clip_polygon_axis(
    polygon: np.ndarray,
    *,
    axis: int,
    bound: float,
    keep_greater: bool,
) -> np.ndarray:
    if polygon.shape[0] == 0:
        return polygon

    def inside(point: np.ndarray) -> bool:
        return bool(point[axis] >= bound if keep_greater else point[axis] <= bound)

    output: list[np.ndarray] = []
    previous = polygon[-1]
    previous_inside = inside(previous)
    for current in polygon:
        current_inside = inside(current)
        if current_inside != previous_inside:
            denominator = current[axis] - previous[axis]
            if abs(float(denominator)) > 1.0e-12:
                fraction = (bound - previous[axis]) / denominator
                output.append(previous + fraction * (current - previous))
        if current_inside:
            output.append(current)
        previous = current
        previous_inside = current_inside
    if not output:
        return np.empty((0, 2), dtype=np.float64)
    result: np.ndarray = np.asarray(output, dtype=np.float64)
    return result


def _clip_to_rectangle(
    polygon: np.ndarray,
    bounds: tuple[float, float, float, float],
) -> np.ndarray:
    x_min, x_max, y_min, y_max = bounds
    result = polygon
    for axis, bound, keep_greater in (
        (0, x_min, True),
        (0, x_max, False),
        (1, y_min, True),
        (1, y_max, False),
    ):
        result = _clip_polygon_axis(
            result,
            axis=axis,
            bound=bound,
            keep_greater=keep_greater,
        )
    return result


def _polygon_area(polygon: np.ndarray) -> float:
    if polygon.shape[0] < 3:
        return 0.0
    x = polygon[:, 0]
    y = polygon[:, 1]
    return abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))) * 0.5


def _stable_floor(value: float) -> int:
    tolerance = 1.0e-9 * max(1.0, abs(value))
    return math.floor(value + tolerance)


def _stable_ceil(value: float) -> int:
    tolerance = 1.0e-9 * max(1.0, abs(value))
    return math.ceil(value - tolerance)


def _project_ground_polygon(
    polygon: np.ndarray,
    *,
    height: float,
    center: np.ndarray,
    camera_to_geographic: np.ndarray,
    camera: CameraInfo,
) -> np.ndarray:
    points = np.column_stack(
        (polygon, np.full(polygon.shape[0], height, dtype=np.float64))
    )
    camera_points = (camera_to_geographic.T @ (points - center).T).T
    visible = camera_points[:, 2] > 1.0e-9
    if not bool(np.all(visible)):
        return np.empty((0, 2), dtype=np.float64)
    pixels = np.column_stack(
        (
            camera.fx * camera_points[:, 0] / camera_points[:, 2] + camera.cx,
            camera.fy * camera_points[:, 1] / camera_points[:, 2] + camera.cy,
        )
    )
    result: np.ndarray = np.asarray(pixels, dtype=np.float64)
    return result


def camera_assignment_for_planar_buffer(
    camera: CameraInfo,
    frame: PlanarSceneFrame,
    bounds: tuple[float, float, float, float],
    *,
    crop_margin_pixels: int = 128,
    maximum_nadir_incidence_degrees: float = 75.0,
    minimum_ground_overlap_m2: float = 1.0,
    minimum_crop_size_pixels: int = 32,
) -> CameraBlockAssignment | None:
    """Return a native crop when a calibrated view sees a planar buffer."""
    if camera.width < 1 or camera.height < 1 or camera.fx <= 0 or camera.fy <= 0:
        raise ValueError("camera footprint selection requires valid pinhole intrinsics")
    if crop_margin_pixels < 0:
        raise ValueError("camera crop margin must be non-negative")
    if not 0.0 < maximum_nadir_incidence_degrees < 90.0:
        raise ValueError("maximum nadir incidence must be between 0 and 90 degrees")
    center, camera_to_geographic = _camera_pose(camera, frame)
    forward = camera_to_geographic[:, 2]
    forward_norm = float(np.linalg.norm(forward))
    cosine = -float(forward[2]) / max(forward_norm, 1.0e-12)
    cosine = float(np.clip(cosine, -1.0, 1.0))
    incidence = math.degrees(math.acos(cosine))
    if (
        incidence > maximum_nadir_incidence_degrees
        or center[2] <= frame.terrain_z_max
    ):
        return None

    pixel_polygons: list[np.ndarray] = []
    maximum_overlap = 0.0
    for height in (frame.terrain_z_min, frame.terrain_z_max):
        footprint = _footprint_at_height(
            camera,
            center,
            camera_to_geographic,
            height,
        )
        if footprint is None:
            return None
        clipped = _clip_to_rectangle(footprint, bounds)
        overlap = _polygon_area(clipped)
        maximum_overlap = max(maximum_overlap, overlap)
        if overlap <= 0.0:
            continue
        pixels = _project_ground_polygon(
            clipped,
            height=height,
            center=center,
            camera_to_geographic=camera_to_geographic,
            camera=camera,
        )
        if pixels.shape[0] > 0:
            pixel_polygons.append(pixels)
    if maximum_overlap < minimum_ground_overlap_m2 or not pixel_polygons:
        return None

    pixels = np.concatenate(pixel_polygons, axis=0)
    left = max(0, _stable_floor(float(np.min(pixels[:, 0]))) - crop_margin_pixels)
    top = max(0, _stable_floor(float(np.min(pixels[:, 1]))) - crop_margin_pixels)
    right = min(
        camera.width,
        _stable_ceil(float(np.max(pixels[:, 0]))) + crop_margin_pixels,
    )
    bottom = min(
        camera.height,
        _stable_ceil(float(np.max(pixels[:, 1]))) + crop_margin_pixels,
    )
    if right - left < minimum_crop_size_pixels or bottom - top < minimum_crop_size_pixels:
        return None
    return CameraBlockAssignment(
        crop=NativeImageCrop(
            source_x=left,
            source_y=top,
            width=right - left,
            height=bottom - top,
            source_width=camera.width,
            source_height=camera.height,
        ),
        maximum_ground_overlap_m2=maximum_overlap,
        nadir_incidence_degrees=incidence,
    )


def camera_assignment_for_ground_buffer(
    camera: CameraInfo,
    frame: GeographicSceneFrame,
    bounds: tuple[float, float, float, float],
    *,
    crop_margin_pixels: int = 128,
    maximum_nadir_incidence_degrees: float = 75.0,
    minimum_ground_overlap_m2: float = 1.0,
    minimum_crop_size_pixels: int = 32,
) -> CameraBlockAssignment | None:
    """Return a native crop when a usable calibrated view sees the ground."""

    return camera_assignment_for_planar_buffer(
        camera,
        frame,
        bounds,
        crop_margin_pixels=crop_margin_pixels,
        maximum_nadir_incidence_degrees=maximum_nadir_incidence_degrees,
        minimum_ground_overlap_m2=minimum_ground_overlap_m2,
        minimum_crop_size_pixels=minimum_crop_size_pixels,
    )
