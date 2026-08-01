"""Similarity-transform helpers shared by production and local pipelines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


def estimate_sim3(
    source_points: Sequence[Sequence[float]] | np.ndarray,
    target_points: Sequence[Sequence[float]] | np.ndarray,
) -> dict[str, Any]:
    """Estimate the Sim(3) mapping ``target = scale * R @ source + t``.

    The implementation follows Umeyama's least-squares estimator and rejects
    non-finite, underspecified, or degenerate point configurations.
    """

    source = np.asarray(source_points, dtype=np.float64)
    target = np.asarray(target_points, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target points must have the same Nx3 shape")
    if source.shape[0] < 3:
        raise ValueError("at least three point correspondences are required")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("point correspondences must be finite")

    source_mean = source.mean(axis=0)
    target_mean = target.mean(axis=0)
    source_centered = source - source_mean
    target_centered = target - target_mean
    source_variance = float(np.sum(source_centered**2) / source.shape[0])
    if source_variance <= np.finfo(np.float64).eps:
        raise ValueError("source point configuration is degenerate")

    covariance = target_centered.T @ source_centered / source.shape[0]
    left, singular_values, right_t = np.linalg.svd(covariance)
    reflection = np.eye(3, dtype=np.float64)
    if np.linalg.det(left) * np.linalg.det(right_t) < 0:
        reflection[-1, -1] = -1
    rotation = left @ reflection @ right_t
    scale = float(np.sum(singular_values * np.diag(reflection)) / source_variance)
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("estimated similarity scale is not positive and finite")
    translation = target_mean - scale * rotation @ source_mean

    predicted = (scale * (rotation @ source.T)).T + translation
    residuals = np.linalg.norm(predicted - target, axis=1)
    return {
        "R": rotation.tolist(),
        "scale": scale,
        "t": translation.tolist(),
        "fit": {
            "correspondences": int(source.shape[0]),
            "rmse": float(np.sqrt(np.mean(residuals**2))),
            "max_error": float(np.max(residuals)),
        },
    }


def estimate_weighted_sim3(
    source_points: Sequence[Sequence[float]] | np.ndarray,
    target_points: Sequence[Sequence[float]] | np.ndarray,
    standard_deviations: Sequence[Sequence[float]] | np.ndarray,
    *,
    robust_loss_scale: float = 3.0,
) -> dict[str, Any]:
    """Estimate a covariance-weighted robust Sim(3).

    ``standard_deviations`` contains one positive XYZ standard deviation per
    control point, in target-coordinate units. Residuals are normalized by
    those values before applying a Cauchy loss. The parameterization is
    centred before optimization so projected CRS translations of several
    million metres do not degrade finite-difference conditioning.
    """

    from scipy.optimize import least_squares
    from scipy.spatial.transform import Rotation

    source = np.asarray(source_points, dtype=np.float64)
    target = np.asarray(target_points, dtype=np.float64)
    sigma = np.asarray(standard_deviations, dtype=np.float64)
    if source.shape != target.shape or source.ndim != 2 or source.shape[1] != 3:
        raise ValueError("source and target points must have the same Nx3 shape")
    if sigma.shape != source.shape:
        raise ValueError("standard deviations must have the same Nx3 shape")
    if source.shape[0] < 3:
        raise ValueError("at least three point correspondences are required")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("point correspondences must be finite")
    if not np.isfinite(sigma).all() or np.any(sigma <= 0):
        raise ValueError("standard deviations must be positive and finite")
    if not np.isfinite(robust_loss_scale) or robust_loss_scale <= 0:
        raise ValueError("robust loss scale must be positive and finite")

    initial = estimate_sim3(source, target)
    source_center = np.mean(source, axis=0)
    target_center = np.mean(target, axis=0)
    initial_rotation = Rotation.from_matrix(np.asarray(initial["R"]))
    parameters = np.concatenate(
        [
            initial_rotation.as_rotvec(),
            [np.log(float(initial["scale"]))],
            np.zeros(3, dtype=np.float64),
        ]
    )

    def predict(values: np.ndarray) -> np.ndarray:
        rotation = Rotation.from_rotvec(values[:3]).as_matrix()
        scale = float(np.exp(values[3]))
        offset = values[4:7]
        return (
            scale * (rotation @ (source - source_center).T).T
            + target_center
            + offset
        )

    def residuals(values: np.ndarray) -> np.ndarray:
        return ((predict(values) - target) / sigma).reshape(-1)

    result = least_squares(
        residuals,
        parameters,
        loss="cauchy",
        f_scale=float(robust_loss_scale),
        max_nfev=2_000,
        xtol=1.0e-12,
        ftol=1.0e-12,
        gtol=1.0e-12,
    )
    if not result.success or not np.isfinite(result.x).all():
        raise RuntimeError(f"weighted similarity optimization failed: {result.message}")

    rotation = Rotation.from_rotvec(result.x[:3]).as_matrix()
    scale = float(np.exp(result.x[3]))
    centered_offset = result.x[4:7]
    translation = target_center + centered_offset - scale * rotation @ source_center
    predicted = (scale * (rotation @ source.T)).T + translation
    vector_residuals = predicted - target
    distances = np.linalg.norm(vector_residuals, axis=1)
    normalized = vector_residuals / sigma
    normalized_norms = np.linalg.norm(normalized, axis=1)
    return {
        "R": rotation.tolist(),
        "scale": scale,
        "t": translation.tolist(),
        "fit": {
            "correspondences": int(source.shape[0]),
            "rmse": float(np.sqrt(np.mean(distances**2))),
            "max_error": float(np.max(distances)),
            "weighted_rmse": float(np.sqrt(np.mean(normalized**2))),
            "maximum_normalized_error": float(np.max(normalized_norms)),
            "robust_loss": "cauchy",
            "robust_loss_scale": float(robust_loss_scale),
            "optimizer_evaluations": int(result.nfev),
        },
    }


def alignment_from_named_centers(
    source_centers: Mapping[str, Sequence[float]],
    target_centers: Mapping[str, Sequence[float]],
) -> dict[str, Any]:
    """Estimate a Sim(3) from camera centres matched by image filename."""

    common_names = sorted(set(source_centers) & set(target_centers))
    if len(common_names) < 3:
        raise ValueError("at least three common registered images are required")
    transform = estimate_sim3(
        [source_centers[name] for name in common_names],
        [target_centers[name] for name in common_names],
    )
    transform["fit"]["common_images"] = common_names
    return transform


def compute_reconstruction_alignment(
    source_model: str | Path,
    target_model: str | Path,
) -> dict[str, Any]:
    """Load two COLMAP models and estimate their filename-matched Sim(3)."""

    import pycolmap

    source = pycolmap.Reconstruction(str(source_model))
    target = pycolmap.Reconstruction(str(target_model))
    source_centers = {
        image.name: np.asarray(image.projection_center(), dtype=np.float64)
        for image in source.images.values()
    }
    target_centers = {
        image.name: np.asarray(image.projection_center(), dtype=np.float64)
        for image in target.images.values()
    }
    return alignment_from_named_centers(source_centers, target_centers)


def write_alignment_transform(path: str | Path, transform: dict[str, Any]) -> Path:
    """Write an alignment transform atomically enough for local pipelines."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(transform, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path
