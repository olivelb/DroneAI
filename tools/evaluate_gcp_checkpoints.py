"""Evaluate a georeferenced COLMAP model against independent GCP checkpoints.

The GCP file follows the OpenDroneMap ``gcp_list.txt`` convention. GCPs are
never fed back into bundle adjustment: each checkpoint is triangulated from
its annotated image rays and compared with its surveyed coordinate.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from pyproj import CRS, Transformer

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.gcp_control import (
    GcpObservation,
    build_image_lookup,
    intersect_rays as intersect_weighted_rays,
    observation_ray,
    parse_gcp_file,
    project_point,
)


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
    """Return the shared least-squares intersection without covariance."""

    point, _, condition = intersect_weighted_rays(origins, directions)
    return point, condition


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


def _evaluate_point(
    reconstruction: Any,
    point_id: str,
    observations: list[GcpObservation],
    image_lookup: dict[str, Any],
    transformer: Transformer,
    minimum_outlier_threshold_px: float,
    alignment_transform: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_xyz = observations[0].source_xyz
    if any(observation.source_xyz != source_xyz for observation in observations):
        raise ValueError(f"GCP {point_id} has inconsistent surveyed coordinates")
    surveyed_xyz = np.asarray(transformer.transform(*source_xyz), dtype=np.float64)
    surveyed_model_xyz = surveyed_xyz
    if alignment_transform is not None:
        rotation = np.asarray(alignment_transform["R"], dtype=np.float64)
        scale = float(alignment_transform["scale"])
        translation = np.asarray(alignment_transform["t"], dtype=np.float64)
        surveyed_model_xyz = rotation.T @ (surveyed_xyz - translation) / scale
    usable: list[tuple[GcpObservation, Any, np.ndarray, np.ndarray]] = []
    missing_images: list[str] = []
    ray_errors: list[dict[str, str]] = []
    for observation in observations:
        image = image_lookup.get(observation.image_name)
        if image is None:
            missing_images.append(observation.image_name)
            continue
        try:
            origin, direction, _ = observation_ray(
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
        projected = project_point(reconstruction, image, estimated_xyz)
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
        projected_estimated = project_point(reconstruction, image, estimated_xyz)
        projected_surveyed = project_point(
            reconstruction, image, surveyed_model_xyz
        )
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

    estimated_model_xyz = estimated_xyz.copy()
    if alignment_transform is not None:
        estimated_xyz = scale * rotation @ estimated_xyz + translation
    delta = estimated_xyz - surveyed_xyz
    return {
        "point_id": point_id,
        "status": "ok",
        "surveyed_xyz": surveyed_xyz.tolist(),
        "estimated_xyz": estimated_xyz.tolist(),
        "triangulated_model_xyz": estimated_model_xyz.tolist(),
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
    camera_references_path: Path | None = None,
) -> dict[str, Any]:
    import pycolmap

    source_crs, observations = parse_gcp_file(gcp_path)
    destination_crs = metric_projected_crs(model_crs)
    transformer = Transformer.from_crs(source_crs, destination_crs, always_xy=True)
    reconstruction = pycolmap.Reconstruction(str(model_path))
    alignment_transform = None
    if camera_references_path is not None:
        from shared.geo_alignment import alignment_from_named_centers

        references = {}
        for line in camera_references_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            name, x, y, z = line.rsplit(maxsplit=3)
            references[name] = (float(x), float(y), float(z))
        centers = {
            image.name: np.asarray(image.projection_center(), dtype=np.float64)
            for image in reconstruction.images.values()
        }
        alignment_transform = alignment_from_named_centers(centers, references)
    lookup = build_image_lookup(reconstruction)
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
            alignment_transform,
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
        "camera_reference_alignment": alignment_transform,
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
    parser.add_argument(
        "--camera-references",
        type=Path,
        help=(
            "Optional COLMAP name/X/Y/Z reference file used to georeference a "
            "raw model independently of the GCP checkpoints."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = evaluate(
        args.model,
        args.gcp_file,
        args.model_crs,
        args.minimum_outlier_threshold_px,
        args.camera_references,
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
