"""Ground-control parsing, triangulation, and covariance-weighted alignment."""

from __future__ import annotations

import csv
import math
import os
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Transformer

from shared.geo_alignment import estimate_sim3, estimate_weighted_sim3
from shared.checksums import sha256_file


ADJUSTMENT_ROLES = {"adjustment", "adjust", "control", "gcp"}
CHECKPOINT_ROLES = {"checkpoint", "check", "validation", "verify"}
DISABLED_ROLES = {"disabled", "ignore", "ignored", "off"}


@dataclass(frozen=True)
class GcpObservation:
    point_id: str
    source_xyz: tuple[float, float, float]
    pixel_xy: tuple[float, float]
    image_name: str


@dataclass(frozen=True)
class GcpAccuracy:
    horizontal_m: float
    vertical_m: float
    image_px: float
    role: str = "adjustment"


file_sha256 = sha256_file


def prepare_gcp_assets(
    dataset_root: str | Path,
    workspace_root: str | Path,
) -> dict[str, Any]:
    """Copy the optional GCP pair from a downloaded dataset into workspace.

    Files are discovered recursively by the unambiguous names
    ``gcp_list.txt`` and ``gcp_accuracy.csv``. Stale workspace copies are
    removed when the source dataset no longer contains them.
    """

    dataset = Path(dataset_root)
    workspace = Path(workspace_root) / "gcp"

    def unique_named(filename: str) -> Path | None:
        matches = sorted(
            path
            for path in dataset.rglob("*")
            if path.is_file() and path.name.lower() == filename
        )
        if len(matches) > 1:
            relative = ", ".join(
                path.relative_to(dataset).as_posix() for path in matches
            )
            raise ValueError(f"multiple {filename} files found: {relative}")
        return matches[0] if matches else None

    sources = {
        "gcp_path": unique_named("gcp_list.txt"),
        "accuracy_path": unique_named("gcp_accuracy.csv"),
    }
    if sources["accuracy_path"] is not None and sources["gcp_path"] is None:
        raise ValueError("gcp_accuracy.csv requires a gcp_list.txt in the dataset")
    destinations = {
        "gcp_path": workspace / "gcp_list.txt",
        "accuracy_path": workspace / "gcp_accuracy.csv",
    }
    changed = False
    for key, source in sources.items():
        destination = destinations[key]
        if source is None:
            if destination.exists():
                destination.unlink()
                changed = True
            continue
        workspace.mkdir(parents=True, exist_ok=True)
        if destination.exists() and file_sha256(destination) == file_sha256(source):
            continue
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        changed = True
    if workspace.exists() and not any(workspace.iterdir()):
        workspace.rmdir()
    return {
        "gcp_path": (
            str(destinations["gcp_path"])
            if destinations["gcp_path"].is_file()
            else None
        ),
        "accuracy_path": (
            str(destinations["accuracy_path"])
            if destinations["accuracy_path"].is_file()
            else None
        ),
        "changed": changed,
    }


def parse_gcp_file(path: str | Path) -> tuple[str, list[GcpObservation]]:
    """Parse the OpenDroneMap ``gcp_list.txt`` convention."""

    source = Path(path)
    lines = [
        line.strip()
        for line in source.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"empty GCP file: {source}")
    source_crs = lines[0]
    CRS.from_user_input(source_crs)
    observations: list[GcpObservation] = []
    for line_number, line in enumerate(lines[1:], start=2):
        fields = line.split()
        if len(fields) < 7:
            raise ValueError(
                f"{source}:{line_number}: expected at least 7 fields, got {len(fields)}"
            )
        try:
            source_xyz = tuple(float(value) for value in fields[0:3])
            pixel_xy = tuple(float(value) for value in fields[3:5])
        except ValueError as error:
            raise ValueError(f"{source}:{line_number}: invalid numeric field") from error
        observations.append(
            GcpObservation(
                point_id=fields[6],
                source_xyz=source_xyz,
                pixel_xy=pixel_xy,
                image_name=fields[5],
            )
        )
    if not observations:
        raise ValueError(f"no GCP observations in {source}")
    return source_crs, observations


def normalize_gcp_role(value: str | None) -> str:
    role = str(value or "adjustment").strip().lower()
    if role in ADJUSTMENT_ROLES:
        return "adjustment"
    if role in CHECKPOINT_ROLES:
        return "checkpoint"
    if role in DISABLED_ROLES:
        return "disabled"
    raise ValueError(f"unsupported GCP role: {value}")


def _positive_accuracy(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be numeric") from error
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return parsed


def parse_gcp_accuracy_file(path: str | Path) -> dict[str, GcpAccuracy]:
    """Read per-point standard deviations and roles from CSV.

    Required columns are ``point_id``, ``horizontal_accuracy_m``,
    ``vertical_accuracy_m``, and ``image_accuracy_px``. ``role`` is optional
    and defaults to ``adjustment``.
    """

    source = Path(path)
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "point_id",
            "horizontal_accuracy_m",
            "vertical_accuracy_m",
            "image_accuracy_px",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"{source}: missing GCP accuracy columns: {', '.join(sorted(missing))}"
            )
        result: dict[str, GcpAccuracy] = {}
        for line_number, row in enumerate(reader, start=2):
            point_id = str(row.get("point_id") or "").strip()
            if not point_id:
                raise ValueError(f"{source}:{line_number}: empty point_id")
            if point_id in result:
                raise ValueError(f"{source}:{line_number}: duplicate point_id {point_id}")
            result[point_id] = GcpAccuracy(
                horizontal_m=_positive_accuracy(
                    row.get("horizontal_accuracy_m"),
                    f"{source}:{line_number}: horizontal_accuracy_m",
                ),
                vertical_m=_positive_accuracy(
                    row.get("vertical_accuracy_m"),
                    f"{source}:{line_number}: vertical_accuracy_m",
                ),
                image_px=_positive_accuracy(
                    row.get("image_accuracy_px"),
                    f"{source}:{line_number}: image_accuracy_px",
                ),
                role=normalize_gcp_role(row.get("role")),
            )
    if not result:
        raise ValueError(f"empty GCP accuracy file: {source}")
    return result


def build_image_lookup(reconstruction: Any) -> dict[str, Any]:
    """Index unambiguous reconstruction images by full name and basename."""

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


def observation_ray(
    reconstruction: Any,
    image: Any,
    pixel_xy: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return a normalized world ray and focal length for an image pixel."""

    camera = reconstruction.cameras[image.camera_id]
    camera_ray = camera.cam_ray_from_img(np.asarray(pixel_xy, dtype=np.float64))
    if camera_ray is None:
        raise ValueError("camera model could not undistort the annotated pixel")
    cam_from_world = image.cam_from_world()
    rotation = np.asarray(cam_from_world.rotation.matrix(), dtype=np.float64)
    origin = np.asarray(image.projection_center(), dtype=np.float64)
    direction = rotation.T @ np.asarray(camera_ray, dtype=np.float64)
    focal = float(camera.mean_focal_length())
    return origin, direction / np.linalg.norm(direction), focal


def project_point(
    reconstruction: Any,
    image: Any,
    xyz: np.ndarray,
) -> np.ndarray | None:
    """Project a world point into an image, or return ``None`` when hidden."""

    camera = reconstruction.cameras[image.camera_id]
    point_camera = np.asarray(image.cam_from_world() * xyz, dtype=np.float64)
    if point_camera[2] <= 0:
        return None
    projected = camera.img_from_cam(point_camera)
    return None if projected is None else np.asarray(projected, dtype=np.float64)


def intersect_rays(
    origins: list[np.ndarray],
    directions: list[np.ndarray],
    perpendicular_sigmas: list[float] | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Intersect rays and return point, covariance, and normal condition."""

    if len(origins) != len(directions) or len(origins) < 2:
        raise ValueError("at least two paired origins and directions are required")
    if perpendicular_sigmas is None:
        perpendicular_sigmas = [1.0] * len(origins)
    if len(perpendicular_sigmas) != len(origins):
        raise ValueError("ray standard deviations must match the ray count")
    normal = np.zeros((3, 3), dtype=np.float64)
    right_hand_side = np.zeros(3, dtype=np.float64)
    identity = np.eye(3, dtype=np.float64)
    for origin, direction, sigma in zip(
        origins,
        directions,
        perpendicular_sigmas,
        strict=True,
    ):
        unit_direction = np.asarray(direction, dtype=np.float64)
        unit_direction /= np.linalg.norm(unit_direction)
        sigma = _positive_accuracy(sigma, "ray standard deviation")
        projector = identity - np.outer(unit_direction, unit_direction)
        weight = 1.0 / (sigma * sigma)
        normal += weight * projector
        right_hand_side += weight * projector @ np.asarray(origin, dtype=np.float64)
    condition = float(np.linalg.cond(normal))
    if not math.isfinite(condition) or condition > 1.0e12:
        raise ValueError(f"ill-conditioned ray intersection ({condition:.3g})")
    covariance = np.linalg.inv(normal)
    return np.linalg.solve(normal, right_hand_side), covariance, condition


def _robust_observation_mask(residuals: list[float], threshold_px: float) -> list[bool]:
    values = np.asarray(residuals, dtype=np.float64)
    finite = np.isfinite(values)
    if int(np.sum(finite)) < 2:
        return finite.tolist()
    if int(np.sum(finite)) < 4:
        return finite.tolist()
    finite_values = values[finite]
    center = float(np.median(finite_values))
    mad = float(np.median(np.abs(finite_values - center)))
    threshold = max(float(threshold_px), center + 3.0 * 1.4826 * mad)
    return (finite & (values <= threshold)).tolist()


def _point_accuracy(
    point_id: str,
    configured: dict[str, GcpAccuracy],
    default: GcpAccuracy,
) -> GcpAccuracy:
    return configured.get(point_id, default)


def _gcp_role_metrics(
    point_reports: list[dict[str, Any]],
    role: str,
) -> dict[str, Any] | None:
    selected = [point for point in point_reports if point["role"] == role]
    if not selected:
        return None
    horizontal = np.asarray(
        [point["horizontal_error_m"] for point in selected], dtype=np.float64
    )
    vertical = np.asarray(
        [point["vertical_error_m"] for point in selected], dtype=np.float64
    )
    euclidean = np.asarray(
        [point["euclidean_error_m"] for point in selected], dtype=np.float64
    )
    normalized = np.asarray(
        [point["normalized_error_norm_sigma"] for point in selected],
        dtype=np.float64,
    )
    return {
        "points": len(selected),
        "horizontal_rmse_m": float(np.sqrt(np.mean(horizontal**2))),
        "vertical_rmse_m": float(np.sqrt(np.mean(vertical**2))),
        "euclidean_rmse_m": float(np.sqrt(np.mean(euclidean**2))),
        "maximum_horizontal_error_m": float(np.max(horizontal)),
        "maximum_vertical_error_m": float(np.max(vertical)),
        "maximum_normalized_error_sigma": float(np.max(normalized)),
    }


def assess_gcp_alignment_quality(
    report: dict[str, Any],
    *,
    require_checkpoints: bool = False,
    minimum_checkpoint_count: int = 1,
    maximum_checkpoint_horizontal_rmse_m: float = 0.10,
    maximum_checkpoint_vertical_rmse_m: float = 0.20,
    maximum_checkpoint_normalized_error_sigma: float = 5.0,
    minimum_adjustment_baseline_m: float = 0.0,
) -> dict[str, Any]:
    """Evaluate whether a fitted GCP transform is safe to promote.

    Adjustment residuals describe fit, not independent accuracy.  Missions
    without checkpoints may still be processed for backwards compatibility,
    but are explicitly labelled ``accepted-unverified`` and never presented as
    independently verified.
    """

    if minimum_checkpoint_count < 1:
        raise ValueError("minimum checkpoint count must be at least one")
    thresholds = (
        maximum_checkpoint_horizontal_rmse_m,
        maximum_checkpoint_vertical_rmse_m,
        maximum_checkpoint_normalized_error_sigma,
        minimum_adjustment_baseline_m,
    )
    if not all(math.isfinite(float(value)) and float(value) >= 0 for value in thresholds):
        raise ValueError("GCP quality thresholds must be finite and non-negative")

    points = list(report.get("points") or [])
    adjustment = [point for point in points if point.get("role") == "adjustment"]
    checkpoint_metrics = _gcp_role_metrics(points, "checkpoint")
    adjustment_metrics = _gcp_role_metrics(points, "adjustment")
    surveyed_xy = np.asarray(
        [point["surveyed_xyz"][:2] for point in adjustment], dtype=np.float64
    )
    if len(surveyed_xy) >= 2:
        deltas = surveyed_xy[:, None, :] - surveyed_xy[None, :, :]
        adjustment_baseline = float(
            np.max(np.linalg.norm(deltas, axis=2))
        )
    else:
        adjustment_baseline = 0.0

    checks: list[dict[str, Any]] = []

    def add_check(name: str, actual: Any, limit: Any, passed: bool) -> None:
        checks.append(
            {"name": name, "actual": actual, "limit": limit, "passed": passed}
        )

    add_check(
        "adjustment_baseline_m",
        adjustment_baseline,
        {"minimum": float(minimum_adjustment_baseline_m)},
        adjustment_baseline >= float(minimum_adjustment_baseline_m),
    )
    checkpoint_count = 0 if checkpoint_metrics is None else checkpoint_metrics["points"]
    if checkpoint_count == 0:
        add_check(
            "independent_checkpoints",
            0,
            {"minimum": minimum_checkpoint_count, "required": require_checkpoints},
            not require_checkpoints,
        )
        verification = "unverified-no-checkpoints"
    else:
        add_check(
            "independent_checkpoints",
            checkpoint_count,
            {"minimum": minimum_checkpoint_count, "required": True},
            checkpoint_count >= minimum_checkpoint_count,
        )
        add_check(
            "checkpoint_horizontal_rmse_m",
            checkpoint_metrics["horizontal_rmse_m"],
            {"maximum": float(maximum_checkpoint_horizontal_rmse_m)},
            checkpoint_metrics["horizontal_rmse_m"]
            <= float(maximum_checkpoint_horizontal_rmse_m),
        )
        add_check(
            "checkpoint_vertical_rmse_m",
            checkpoint_metrics["vertical_rmse_m"],
            {"maximum": float(maximum_checkpoint_vertical_rmse_m)},
            checkpoint_metrics["vertical_rmse_m"]
            <= float(maximum_checkpoint_vertical_rmse_m),
        )
        add_check(
            "checkpoint_normalized_error_sigma",
            checkpoint_metrics["maximum_normalized_error_sigma"],
            {"maximum": float(maximum_checkpoint_normalized_error_sigma)},
            checkpoint_metrics["maximum_normalized_error_sigma"]
            <= float(maximum_checkpoint_normalized_error_sigma),
        )
        verification = "independently-verified"

    accepted = all(check["passed"] for check in checks)
    return {
        "schema_version": 1,
        "accepted": accepted,
        "status": (
            "rejected"
            if not accepted
            else "accepted-verified"
            if checkpoint_count
            else "accepted-unverified"
        ),
        "verification": verification,
        "adjustment_metrics": adjustment_metrics,
        "checkpoint_metrics": checkpoint_metrics,
        "adjustment_baseline_m": adjustment_baseline,
        "checks": checks,
    }


def build_weighted_gcp_alignment(
    model_path: str | Path,
    gcp_path: str | Path,
    destination_crs: str,
    *,
    accuracy_path: str | Path | None = None,
    default_horizontal_accuracy_m: float = 0.02,
    default_vertical_accuracy_m: float = 0.03,
    default_image_accuracy_px: float = 1.0,
    robust_loss_scale: float = 3.0,
    require_checkpoints: bool = False,
    minimum_checkpoint_count: int = 1,
    maximum_checkpoint_horizontal_rmse_m: float = 0.10,
    maximum_checkpoint_vertical_rmse_m: float = 0.20,
    maximum_checkpoint_normalized_error_sigma: float = 5.0,
    minimum_adjustment_baseline_m: float = 0.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Triangulate GCP image observations and fit a weighted robust Sim(3)."""

    import pycolmap

    source_crs, observations = parse_gcp_file(gcp_path)
    destination = CRS.from_user_input(destination_crs)
    if not destination.is_projected:
        raise ValueError("GCP destination CRS must be projected")
    transformer = Transformer.from_crs(source_crs, destination, always_xy=True)
    default_accuracy = GcpAccuracy(
        horizontal_m=_positive_accuracy(
            default_horizontal_accuracy_m, "default horizontal GCP accuracy"
        ),
        vertical_m=_positive_accuracy(
            default_vertical_accuracy_m, "default vertical GCP accuracy"
        ),
        image_px=_positive_accuracy(
            default_image_accuracy_px, "default image annotation accuracy"
        ),
    )
    configured = (
        parse_gcp_accuracy_file(accuracy_path) if accuracy_path is not None else {}
    )
    grouped: dict[str, list[GcpObservation]] = defaultdict(list)
    for observation in observations:
        grouped[observation.point_id].append(observation)
    unknown_accuracy_points = sorted(set(configured) - set(grouped))
    if unknown_accuracy_points:
        raise ValueError(
            "gcp_accuracy.csv contains point IDs absent from gcp_list.txt: "
            + ", ".join(unknown_accuracy_points)
        )
    reconstruction = pycolmap.Reconstruction(str(model_path))
    lookup = build_image_lookup(reconstruction)

    triangulated: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for point_id in sorted(grouped):
        point_observations = grouped[point_id]
        surveyed_source = point_observations[0].source_xyz
        if any(item.source_xyz != surveyed_source for item in point_observations):
            raise ValueError(f"GCP {point_id} has inconsistent surveyed coordinates")
        accuracy = _point_accuracy(point_id, configured, default_accuracy)
        if accuracy.role == "disabled":
            rejected.append({"point_id": point_id, "reason": "disabled"})
            continue
        usable: list[tuple[GcpObservation, Any, np.ndarray, np.ndarray, float]] = []
        for observation in point_observations:
            image = lookup.get(observation.image_name)
            if image is None:
                continue
            try:
                origin, direction, focal = observation_ray(
                    reconstruction, image, observation.pixel_xy
                )
            except ValueError:
                continue
            usable.append((observation, image, origin, direction, focal))
        if len(usable) < 2:
            rejected.append(
                {
                    "point_id": point_id,
                    "reason": "insufficient_registered_observations",
                    "usable_observations": len(usable),
                }
            )
            continue

        initial, _, _ = intersect_rays(
            [item[2] for item in usable],
            [item[3] for item in usable],
        )
        reprojection = []
        for observation, image, _, _, _ in usable:
            projected = project_point(reconstruction, image, initial)
            reprojection.append(
                float("inf")
                if projected is None
                else float(
                    np.linalg.norm(
                        projected - np.asarray(observation.pixel_xy, dtype=np.float64)
                    )
                )
            )
        mask = _robust_observation_mask(reprojection, 3.0 * accuracy.image_px)
        inliers = [item for item, keep in zip(usable, mask, strict=True) if keep]
        if len(inliers) < 2:
            rejected.append(
                {
                    "point_id": point_id,
                    "reason": "insufficient_reprojection_inliers",
                    "usable_observations": len(usable),
                    "inlier_observations": len(inliers),
                }
            )
            continue
        ranges = [max(float(np.linalg.norm(initial - item[2])), 1.0e-6) for item in inliers]
        ray_sigmas = [
            max(distance * accuracy.image_px / item[4], 1.0e-6)
            for item, distance in zip(inliers, ranges, strict=True)
        ]
        estimated, covariance, condition = intersect_rays(
            [item[2] for item in inliers],
            [item[3] for item in inliers],
            ray_sigmas,
        )
        surveyed = np.asarray(
            transformer.transform(*surveyed_source), dtype=np.float64
        )
        triangulated.append(
            {
                "point_id": point_id,
                "role": accuracy.role,
                "source_xyz": estimated,
                "surveyed_xyz": surveyed,
                "source_covariance": covariance,
                "survey_accuracy_xyz_m": np.asarray(
                    [accuracy.horizontal_m, accuracy.horizontal_m, accuracy.vertical_m],
                    dtype=np.float64,
                ),
                "image_accuracy_px": accuracy.image_px,
                "annotated_observations": len(point_observations),
                "usable_observations": len(usable),
                "inlier_observations": len(inliers),
                "ray_intersection_condition": condition,
            }
        )

    adjustment = [point for point in triangulated if point["role"] == "adjustment"]
    if len(adjustment) < 3:
        raise ValueError(
            f"GCP adjustment requires at least three triangulated adjustment points; "
            f"got {len(adjustment)}"
        )
    source_points = np.asarray([point["source_xyz"] for point in adjustment])
    target_points = np.asarray([point["surveyed_xyz"] for point in adjustment])
    if np.linalg.matrix_rank(source_points - np.mean(source_points, axis=0)) < 2:
        raise ValueError("GCP adjustment points are collinear in the reconstruction")
    transform = estimate_sim3(source_points, target_points)
    covariance_iterations = 3
    for _ in range(covariance_iterations):
        current_scale = float(transform["scale"])
        current_rotation = np.asarray(transform["R"], dtype=np.float64)
        effective_sigmas = []
        for point in adjustment:
            survey_variance = np.square(point["survey_accuracy_xyz_m"])
            triangulation_covariance = (
                current_scale
                * current_scale
                * current_rotation
                @ point["source_covariance"]
                @ current_rotation.T
            )
            triangulation_variance = np.maximum(
                np.diag(triangulation_covariance), 0.0
            )
            effective_sigmas.append(
                np.sqrt(survey_variance + triangulation_variance)
            )
        transform = estimate_weighted_sim3(
            source_points,
            target_points,
            np.asarray(effective_sigmas),
            robust_loss_scale=robust_loss_scale,
        )
    rotation = np.asarray(transform["R"], dtype=np.float64)
    scale = float(transform["scale"])
    translation = np.asarray(transform["t"], dtype=np.float64)
    point_reports = []
    for point in triangulated:
        predicted = scale * rotation @ point["source_xyz"] + translation
        delta = predicted - point["surveyed_xyz"]
        triangulation_covariance = (
            scale
            * scale
            * rotation
            @ point["source_covariance"]
            @ rotation.T
        )
        triangulation_std = np.sqrt(
            np.maximum(np.diag(triangulation_covariance), 0.0)
        )
        effective_std = np.sqrt(
            np.square(point["survey_accuracy_xyz_m"])
            + np.square(triangulation_std)
        )
        normalized_delta = delta / effective_std
        point_reports.append(
            {
                "point_id": point["point_id"],
                "role": point["role"],
                "surveyed_xyz": point["surveyed_xyz"].tolist(),
                "triangulated_source_xyz": point["source_xyz"].tolist(),
                "error_xyz_m": delta.tolist(),
                "horizontal_error_m": float(np.linalg.norm(delta[:2])),
                "vertical_error_m": abs(float(delta[2])),
                "euclidean_error_m": float(np.linalg.norm(delta)),
                "survey_accuracy_xyz_m": point["survey_accuracy_xyz_m"].tolist(),
                "triangulation_std_xyz_m": triangulation_std.tolist(),
                "effective_std_xyz_m": effective_std.tolist(),
                "normalized_error_xyz_sigma": normalized_delta.tolist(),
                "normalized_error_norm_sigma": float(
                    np.linalg.norm(normalized_delta)
                ),
                "image_accuracy_px": point["image_accuracy_px"],
                "annotated_observations": point["annotated_observations"],
                "usable_observations": point["usable_observations"],
                "inlier_observations": point["inlier_observations"],
                "ray_intersection_condition": point["ray_intersection_condition"],
            }
        )
    transform["fit"].update(
        {
            "source": "covariance_weighted_gcp",
            "adjustment_point_ids": [point["point_id"] for point in adjustment],
            "checkpoint_point_ids": [
                point["point_id"]
                for point in triangulated
                if point["role"] == "checkpoint"
            ],
            "covariance_iterations": covariance_iterations,
        }
    )
    report = {
        "schema_version": 1,
        "model_path": str(Path(model_path).resolve()),
        "gcp_path": str(Path(gcp_path).resolve()),
        "gcp_sha256": file_sha256(gcp_path),
        "accuracy_path": (
            str(Path(accuracy_path).resolve()) if accuracy_path is not None else None
        ),
        "accuracy_sha256": (
            file_sha256(accuracy_path) if accuracy_path is not None else None
        ),
        "source_crs": CRS.from_user_input(source_crs).to_string(),
        "destination_crs": destination.to_string(),
        "adjustment_points": len(adjustment),
        "checkpoint_points": sum(
            point["role"] == "checkpoint" for point in triangulated
        ),
        "rejected_points": rejected,
        "transform": transform,
        "points": point_reports,
        "defaults": {
            "horizontal_accuracy_m": default_accuracy.horizontal_m,
            "vertical_accuracy_m": default_accuracy.vertical_m,
            "image_accuracy_px": default_accuracy.image_px,
            "robust_loss_scale": robust_loss_scale,
        },
    }
    report["quality_gate"] = assess_gcp_alignment_quality(
        report,
        require_checkpoints=require_checkpoints,
        minimum_checkpoint_count=minimum_checkpoint_count,
        maximum_checkpoint_horizontal_rmse_m=(
            maximum_checkpoint_horizontal_rmse_m
        ),
        maximum_checkpoint_vertical_rmse_m=(
            maximum_checkpoint_vertical_rmse_m
        ),
        maximum_checkpoint_normalized_error_sigma=(
            maximum_checkpoint_normalized_error_sigma
        ),
        minimum_adjustment_baseline_m=minimum_adjustment_baseline_m,
    )
    return transform, report


def write_transformed_reconstruction(
    model_path: str | Path,
    output_path: str | Path,
    transform: dict[str, Any],
) -> None:
    """Write a COLMAP model in the same target frame as an alignment Sim(3)."""

    import pycolmap

    rotation = np.asarray(transform["R"], dtype=np.float64)
    translation = np.asarray(transform["t"], dtype=np.float64).reshape(3)
    scale = float(transform["scale"])
    if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
        raise ValueError("alignment rotation must be a finite 3x3 matrix")
    if not np.isfinite(translation).all() or not math.isfinite(scale) or scale <= 0:
        raise ValueError("alignment scale and translation must be finite")

    output = Path(output_path)
    temporary = output.with_name(f"{output.name}.tmp")
    shutil.rmtree(temporary, ignore_errors=True)
    reconstruction = pycolmap.Reconstruction(str(model_path))
    reconstruction.transform(
        pycolmap.Sim3d(
            scale,
            pycolmap.Rotation3d(rotation),
            translation,
        )
    )
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        reconstruction.write(temporary)
        if output.exists():
            shutil.rmtree(output)
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
