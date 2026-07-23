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
