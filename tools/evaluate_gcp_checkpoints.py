"""Evaluate a georeferenced COLMAP model against independent GCP checkpoints.

The GCP file follows the OpenDroneMap ``gcp_list.txt`` convention. GCPs are
never fed back into bundle adjustment: each checkpoint is triangulated from
its annotated image rays and compared with its surveyed coordinate.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from pyproj import CRS, Transformer


@dataclass(frozen=True)
class GcpObservation:
    point_id: str
    source_xyz: tuple[float, float, float]
    pixel_xy: tuple[float, float]
    image_name: str


def parse_gcp_file(path: Path) -> tuple[str, list[GcpObservation]]:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"empty GCP file: {path}")
    source_crs = lines[0]
    CRS.from_user_input(source_crs)
    observations: list[GcpObservation] = []
    for line_number, line in enumerate(lines[1:], start=2):
        fields = line.split()
        if len(fields) < 7:
            raise ValueError(
                f"{path}:{line_number}: expected at least 7 fields, got {len(fields)}"
            )
        try:
            source_xyz = tuple(float(value) for value in fields[0:3])
            pixel_xy = tuple(float(value) for value in fields[3:5])
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: invalid numeric field") from error
        observations.append(
            GcpObservation(
                point_id=fields[6],
                source_xyz=source_xyz,
                pixel_xy=pixel_xy,
                image_name=fields[5],
            )
        )
    if not observations:
        raise ValueError(f"no GCP observations in {path}")
    return source_crs, observations


def metric_projected_crs(value: str) -> CRS:
    crs = CRS.from_user_input(value)
    horizontal_axes = crs.axis_info[:2]
    if not crs.is_projected or len(horizontal_axes) != 2:
        raise ValueError(f"model CRS must be projected, got {crs.to_string()}")
    if any(
        not math.isclose(axis.unit_conversion_factor or 0.0, 1.0)
        for axis in horizontal_axes
    ):
        raise ValueError(f"model CRS axes must use metres, got {crs.to_string()}")
    return crs


def intersect_rays(
    origins: list[np.ndarray],
    directions: list[np.ndarray],
) -> tuple[np.ndarray, float]:
    """Return the least-squares intersection and normal-matrix condition."""

    if len(origins) != len(directions) or len(origins) < 2:
        raise ValueError("at least two paired origins and directions are required")
    normal = np.zeros((3, 3), dtype=np.float64)
    right_hand_side = np.zeros(3, dtype=np.float64)
    identity = np.eye(3, dtype=np.float64)
    for origin, direction in zip(origins, directions, strict=True):
        unit_direction = np.asarray(direction, dtype=np.float64)
        unit_direction /= np.linalg.norm(unit_direction)
        projector = identity - np.outer(unit_direction, unit_direction)
        normal += projector
        right_hand_side += projector @ np.asarray(origin, dtype=np.float64)
    condition = float(np.linalg.cond(normal))
    if not math.isfinite(condition) or condition > 1.0e12:
        raise ValueError(f"ill-conditioned ray intersection ({condition:.3g})")
    return np.linalg.solve(normal, right_hand_side), condition


def statistics(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "rmse": None,
            "p95": None,
            "maximum": None,
        }
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": len(values),
        "mean": float(np.mean(array)),
        "median": float(np.median(array)),
        "rmse": float(np.sqrt(np.mean(np.square(array)))),
        "p95": float(np.percentile(array, 95)),
        "maximum": float(np.max(array)),
    }


def robust_inlier_mask(
    residuals: list[float], minimum_threshold_px: float
) -> list[bool]:
    if len(residuals) < 4:
        return [True] * len(residuals)
    center = median(residuals)
    mad = median(abs(value - center) for value in residuals)
    threshold = max(minimum_threshold_px, center + 3.0 * 1.4826 * mad)
    mask = [value <= threshold for value in residuals]
    return mask if sum(mask) >= 2 else [True] * len(residuals)


def _image_lookup(reconstruction: Any) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    ambiguous: set[str] = set()
    for image in reconstruction.images.values():
        for key in (image.name, Path(image.name).name):
            if key in lookup and lookup[key].image_id != image.image_id:
                ambiguous.add(key)
            else:
                lookup[key] = image
    for key in ambiguous:
        lookup.pop(key, None)
    return lookup


def _ray_for_observation(
    reconstruction: Any,
    image: Any,
    pixel_xy: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    camera = reconstruction.cameras[image.camera_id]
    camera_ray = camera.cam_ray_from_img(np.asarray(pixel_xy, dtype=np.float64))
    if camera_ray is None:
        raise ValueError("camera model could not undistort the annotated pixel")
    cam_from_world = image.cam_from_world()
    rotation = np.asarray(cam_from_world.rotation.matrix(), dtype=np.float64)
    origin = np.asarray(image.projection_center(), dtype=np.float64)
    direction = rotation.T @ np.asarray(camera_ray, dtype=np.float64)
    return origin, direction / np.linalg.norm(direction)


def _project_point(
    reconstruction: Any, image: Any, xyz: np.ndarray
) -> np.ndarray | None:
    camera = reconstruction.cameras[image.camera_id]
    point_camera = np.asarray(image.cam_from_world() * xyz, dtype=np.float64)
    if point_camera[2] <= 0.0:
        return None
    projected = camera.img_from_cam(point_camera)
    return None if projected is None else np.asarray(projected, dtype=np.float64)


def _evaluate_point(
    reconstruction: Any,
    point_id: str,
    observations: list[GcpObservation],
    image_lookup: dict[str, Any],
    transformer: Transformer,
    minimum_outlier_threshold_px: float,
) -> dict[str, Any]:
    source_xyz = observations[0].source_xyz
    if any(observation.source_xyz != source_xyz for observation in observations):
        raise ValueError(f"GCP {point_id} has inconsistent surveyed coordinates")
    surveyed_xyz = np.asarray(transformer.transform(*source_xyz), dtype=np.float64)
    usable: list[tuple[GcpObservation, Any, np.ndarray, np.ndarray]] = []
    missing_images: list[str] = []
    ray_errors: list[dict[str, str]] = []
    for observation in observations:
        image = image_lookup.get(observation.image_name)
        if image is None:
            missing_images.append(observation.image_name)
            continue
        try:
            origin, direction = _ray_for_observation(
                reconstruction, image, observation.pixel_xy
            )
        except ValueError as error:
            ray_errors.append({"image": observation.image_name, "error": str(error)})
            continue
        usable.append((observation, image, origin, direction))
    if len(usable) < 2:
        return {
            "point_id": point_id,
            "status": "insufficient_registered_observations",
            "surveyed_xyz": surveyed_xyz.tolist(),
            "annotated_observations": len(observations),
            "usable_observations": len(usable),
            "missing_images": sorted(missing_images),
            "ray_errors": ray_errors,
        }

    estimated_xyz, condition = intersect_rays(
        [item[2] for item in usable], [item[3] for item in usable]
    )
    initial_residuals: list[float] = []
    for observation, image, _, _ in usable:
        projected = _project_point(reconstruction, image, estimated_xyz)
        initial_residuals.append(
            float("inf")
            if projected is None
            else float(np.linalg.norm(projected - np.asarray(observation.pixel_xy)))
        )
    inlier_mask = robust_inlier_mask(initial_residuals, minimum_outlier_threshold_px)
    inliers = [item for item, keep in zip(usable, inlier_mask, strict=True) if keep]
    if len(inliers) != len(usable):
        estimated_xyz, condition = intersect_rays(
            [item[2] for item in inliers], [item[3] for item in inliers]
        )

    triangulated_reprojection: list[float] = []
    surveyed_reprojection: list[float] = []
    observation_results: list[dict[str, Any]] = []
    inlier_images = {item[0].image_name for item in inliers}
    for observation, image, _, _ in usable:
        observed = np.asarray(observation.pixel_xy, dtype=np.float64)
        projected_estimated = _project_point(reconstruction, image, estimated_xyz)
        projected_surveyed = _project_point(reconstruction, image, surveyed_xyz)
        estimated_residual = (
            None
            if projected_estimated is None
            else float(np.linalg.norm(projected_estimated - observed))
        )
        surveyed_residual = (
            None
            if projected_surveyed is None
            else float(np.linalg.norm(projected_surveyed - observed))
        )
        if estimated_residual is not None:
            triangulated_reprojection.append(estimated_residual)
        if surveyed_residual is not None:
            surveyed_reprojection.append(surveyed_residual)
        observation_results.append(
            {
                "image": observation.image_name,
                "pixel_xy": list(observation.pixel_xy),
                "inlier": observation.image_name in inlier_images,
                "triangulated_reprojection_error_px": estimated_residual,
                "surveyed_reprojection_error_px": surveyed_residual,
            }
        )

    delta = estimated_xyz - surveyed_xyz
    return {
        "point_id": point_id,
        "status": "ok",
        "surveyed_xyz": surveyed_xyz.tolist(),
        "estimated_xyz": estimated_xyz.tolist(),
        "error_xyz": delta.tolist(),
        "horizontal_error_m": float(np.linalg.norm(delta[:2])),
        "vertical_error_m": abs(float(delta[2])),
        "euclidean_error_m": float(np.linalg.norm(delta)),
        "ray_intersection_condition": condition,
        "annotated_observations": len(observations),
        "usable_observations": len(usable),
        "inlier_observations": len(inliers),
        "missing_images": sorted(missing_images),
        "ray_errors": ray_errors,
        "triangulated_reprojection_error_px": statistics(triangulated_reprojection),
        "surveyed_reprojection_error_px": statistics(surveyed_reprojection),
        "observations": observation_results,
    }


def evaluate(
    model_path: Path,
    gcp_path: Path,
    model_crs: str,
    minimum_outlier_threshold_px: float,
) -> dict[str, Any]:
    import pycolmap

    source_crs, observations = parse_gcp_file(gcp_path)
    destination_crs = metric_projected_crs(model_crs)
    transformer = Transformer.from_crs(source_crs, destination_crs, always_xy=True)
    reconstruction = pycolmap.Reconstruction(str(model_path))
    lookup = _image_lookup(reconstruction)
    grouped: dict[str, list[GcpObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.point_id].append(observation)
    points = [
        _evaluate_point(
            reconstruction,
            point_id,
            grouped[point_id],
            lookup,
            transformer,
            minimum_outlier_threshold_px,
        )
        for point_id in sorted(grouped)
    ]
    successful = [point for point in points if point["status"] == "ok"]
    horizontal = [point["horizontal_error_m"] for point in successful]
    vertical = [point["vertical_error_m"] for point in successful]
    euclidean = [point["euclidean_error_m"] for point in successful]
    surveyed_reprojection = [
        observation["surveyed_reprojection_error_px"]
        for point in successful
        for observation in point["observations"]
        if observation["surveyed_reprojection_error_px"] is not None
    ]
    triangulated_reprojection = [
        observation["triangulated_reprojection_error_px"]
        for point in successful
        for observation in point["observations"]
        if observation["triangulated_reprojection_error_px"] is not None
        and observation["inlier"]
    ]
    return {
        "schema_version": 1,
        "model_path": str(model_path.resolve()),
        "gcp_path": str(gcp_path.resolve()),
        "source_crs": CRS.from_user_input(source_crs).to_string(),
        "model_crs": destination_crs.to_string(),
        "registered_images": len(reconstruction.images),
        "checkpoint_count": len(points),
        "successful_checkpoint_count": len(successful),
        "summary": {
            "horizontal_error_m": statistics(horizontal),
            "vertical_error_m": statistics(vertical),
            "euclidean_error_m": statistics(euclidean),
            "surveyed_reprojection_error_px": statistics(surveyed_reprojection),
            "triangulated_reprojection_error_px": statistics(triangulated_reprojection),
        },
        "points": points,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path, help="Georeferenced COLMAP model")
    parser.add_argument("gcp_file", type=Path)
    parser.add_argument("--model-crs", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--minimum-outlier-threshold-px", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate(
        args.model,
        args.gcp_file,
        args.model_crs,
        args.minimum_outlier_threshold_px,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
