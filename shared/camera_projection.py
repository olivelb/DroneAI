"""Portable COLMAP camera index and GCP visibility projection.

The worker exports the small JSON index while ``pycolmap`` is available.  The
dashboard can then rank GCP photographs without shipping the COLMAP runtime.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyproj import Transformer


SCHEMA_VERSION = 1
SUPPORTED_MODELS = {
    "SIMPLE_PINHOLE",
    "PINHOLE",
    "SIMPLE_RADIAL",
    "RADIAL",
    "OPENCV",
    "FULL_OPENCV",
    "OPENCV_FISHEYE",
}
MODEL_PARAMETER_COUNTS = {
    "SIMPLE_PINHOLE": 3,
    "PINHOLE": 4,
    "SIMPLE_RADIAL": 4,
    "RADIAL": 5,
    "OPENCV": 8,
    "FULL_OPENCV": 12,
    "OPENCV_FISHEYE": 8,
}


@dataclass(frozen=True)
class CameraProjection:
    image_name: str
    width: int
    height: int
    model: str
    params: tuple[float, ...]
    cam_from_world: tuple[tuple[float, float, float, float], ...]


@dataclass(frozen=True)
class CameraProjectionIndex:
    crs: str
    cameras: tuple[CameraProjection, ...]


@dataclass(frozen=True)
class ProjectedImageCandidate:
    image_name: str
    pixel_x: float
    pixel_y: float
    image_width_px: int
    image_height_px: int
    center_score: float


def _finite_float(value: Any, label: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def build_camera_projection_index(model_path: str | Path, crs: str) -> dict[str, Any]:
    """Serialize registered COLMAP cameras into a runtime-neutral index."""

    import pycolmap  # type: ignore[import-not-found]

    reconstruction = pycolmap.Reconstruction(str(model_path))
    images: list[dict[str, Any]] = []
    for image in sorted(reconstruction.images.values(), key=lambda item: item.name):
        camera = reconstruction.cameras[image.camera_id]
        model = str(camera.model_name)
        if model not in SUPPORTED_MODELS:
            continue
        matrix = image.cam_from_world().matrix()
        images.append(
            {
                "image_name": image.name,
                "width": int(camera.width),
                "height": int(camera.height),
                "model": model,
                "params": [float(value) for value in camera.params],
                "cam_from_world": [[float(matrix[row, column]) for column in range(4)] for row in range(3)],
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "crs": crs,
        "images": images,
    }


def write_camera_projection_index(
    model_path: str | Path,
    crs: str,
    destination: str | Path,
) -> None:
    """Write a deterministic camera index for API-side GCP ranking."""

    payload = build_camera_projection_index(model_path, crs)
    Path(destination).write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def parse_camera_projection_index(payload: bytes) -> CameraProjectionIndex:
    """Validate a bounded camera index read from object storage."""

    raw = json.loads(payload.decode("utf-8-sig"))
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported camera projection index schema")
    crs = str(raw.get("crs") or "").strip()
    if not crs:
        raise ValueError("camera projection index CRS is missing")
    cameras: list[CameraProjection] = []
    seen: set[str] = set()
    for index, item in enumerate(raw.get("images") or [], start=1):
        name = str(item.get("image_name") or "").strip()
        model = str(item.get("model") or "").strip()
        width = int(item.get("width") or 0)
        height = int(item.get("height") or 0)
        if not name or name in seen:
            raise ValueError(f"camera index image {index}: invalid or duplicate name")
        if model not in SUPPORTED_MODELS:
            continue
        if width < 1 or height < 1:
            raise ValueError(f"camera index image {name}: invalid dimensions")
        params = tuple(_finite_float(value, f"camera {name} parameter") for value in item.get("params", []))
        if len(params) != MODEL_PARAMETER_COUNTS[model]:
            raise ValueError(f"camera index image {name}: {model} expects {MODEL_PARAMETER_COUNTS[model]} parameters")
        matrix_raw = item.get("cam_from_world") or []
        if len(matrix_raw) != 3 or any(len(row) != 4 for row in matrix_raw):
            raise ValueError(f"camera index image {name}: expected a 3x4 transform")
        matrix = tuple(
            (
                _finite_float(row[0], f"camera {name} transform"),
                _finite_float(row[1], f"camera {name} transform"),
                _finite_float(row[2], f"camera {name} transform"),
                _finite_float(row[3], f"camera {name} transform"),
            )
            for row in matrix_raw
        )
        cameras.append(CameraProjection(name, width, height, model, params, matrix))
        seen.add(name)
    return CameraProjectionIndex(crs=crs, cameras=tuple(cameras))


def _distort(model: str, params: tuple[float, ...], x: float, y: float) -> tuple[float, float]:
    if model in {"SIMPLE_PINHOLE", "PINHOLE"}:
        return x, y
    if model == "SIMPLE_RADIAL":
        radial = 1.0 + params[3] * (x * x + y * y)
        return x * radial, y * radial
    if model == "RADIAL":
        radius2 = x * x + y * y
        radial = 1.0 + params[3] * radius2 + params[4] * radius2 * radius2
        return x * radial, y * radial
    if model in {"OPENCV", "FULL_OPENCV"}:
        radius2 = x * x + y * y
        k1, k2, p1, p2 = params[4:8]
        numerator = 1.0 + k1 * radius2 + k2 * radius2**2
        if model == "FULL_OPENCV":
            k3, k4, k5, k6 = params[8:12]
            numerator += k3 * radius2**3
            denominator = 1.0 + k4 * radius2 + k5 * radius2**2 + k6 * radius2**3
            radial = numerator / denominator
        else:
            radial = numerator
        return (
            x * radial + 2.0 * p1 * x * y + p2 * (radius2 + 2.0 * x * x),
            y * radial + p1 * (radius2 + 2.0 * y * y) + 2.0 * p2 * x * y,
        )
    if model == "OPENCV_FISHEYE":
        radius = math.hypot(x, y)
        if radius < 1.0e-12:
            return x, y
        theta = math.atan(radius)
        theta2 = theta * theta
        k1, k2, k3, k4 = params[4:8]
        distorted_theta = theta * (1.0 + k1 * theta2 + k2 * theta2**2 + k3 * theta2**3 + k4 * theta2**4)
        scale = distorted_theta / radius
        return x * scale, y * scale
    raise ValueError(f"unsupported camera model: {model}")


def project_world_point(
    camera: CameraProjection,
    world_xyz: tuple[float, float, float],
) -> tuple[float, float] | None:
    """Project one world point with COLMAP camera-model conventions."""

    camera_xyz = tuple(
        sum(row[column] * world_xyz[column] for column in range(3)) + row[3] for row in camera.cam_from_world
    )
    if camera_xyz[2] <= 0:
        return None
    normalized_x = camera_xyz[0] / camera_xyz[2]
    normalized_y = camera_xyz[1] / camera_xyz[2]
    distorted_x, distorted_y = _distort(camera.model, camera.params, normalized_x, normalized_y)
    if camera.model in {"SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL"}:
        focal, center_x, center_y = camera.params[:3]
        return focal * distorted_x + center_x, focal * distorted_y + center_y
    focal_x, focal_y, center_x, center_y = camera.params[:4]
    return focal_x * distorted_x + center_x, focal_y * distorted_y + center_y


def rank_projected_image_candidates(
    *,
    longitude: float,
    latitude: float,
    altitude_m: float,
    camera_index: CameraProjectionIndex,
    limit: int,
    existing_image_names: set[str] | None = None,
    border_margin_ratio: float = 0.0,
) -> tuple[ProjectedImageCandidate, ...]:
    """Rank registered images where the surveyed point projects inside the frame."""

    if limit < 1:
        raise ValueError("candidate limit must be positive")
    if not 0 <= border_margin_ratio < 0.5:
        raise ValueError("border margin ratio must be in [0, 0.5)")
    transformer = Transformer.from_crs("EPSG:4326", camera_index.crs, always_xy=True)
    x, y, z = transformer.transform(longitude, latitude, altitude_m)
    candidates: list[ProjectedImageCandidate] = []
    excluded = existing_image_names or set()
    for camera in camera_index.cameras:
        if camera.image_name in excluded:
            continue
        projected = project_world_point(camera, (float(x), float(y), float(z)))
        if projected is None:
            continue
        pixel_x, pixel_y = projected
        margin_x = camera.width * border_margin_ratio
        margin_y = camera.height * border_margin_ratio
        if not (margin_x <= pixel_x < camera.width - margin_x):
            continue
        if not (margin_y <= pixel_y < camera.height - margin_y):
            continue
        center_score = math.hypot(
            (pixel_x - camera.width / 2.0) / (camera.width / 2.0),
            (pixel_y - camera.height / 2.0) / (camera.height / 2.0),
        )
        candidates.append(
            ProjectedImageCandidate(
                image_name=camera.image_name,
                pixel_x=pixel_x,
                pixel_y=pixel_y,
                image_width_px=camera.width,
                image_height_px=camera.height,
                center_score=center_score,
            )
        )
    return tuple(sorted(candidates, key=lambda item: (item.center_score, item.image_name)))[:limit]
