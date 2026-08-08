"""Estimate a stable local facade coordinate frame from an SfM model."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class _CameraPose(Protocol):
    R: np.ndarray
    T: np.ndarray


@dataclass(frozen=True)
class FacadeFrame:
    origin: np.ndarray
    world_to_facade: np.ndarray
    inlier_ratio: float
    plane_rmse: float
    camera_side_ratio: float
    median_view_incidence_deg: float
    p90_view_incidence_deg: float
    orientation_source: str = "optimized-camera-optical-axes"

    def as_dict(self) -> dict[str, object]:
        return {
            "origin_model_units": self.origin.tolist(),
            "world_to_facade": self.world_to_facade.tolist(),
            "axes_world": {
                "horizontal": self.world_to_facade[0].tolist(),
                "vertical": self.world_to_facade[1].tolist(),
                "outward_normal": self.world_to_facade[2].tolist(),
            },
            "plane_inlier_ratio": self.inlier_ratio,
            "plane_rmse_model_units": self.plane_rmse,
            "camera_side_ratio": self.camera_side_ratio,
            "orientation_source": self.orientation_source,
            "median_view_incidence_deg": self.median_view_incidence_deg,
            "p90_view_incidence_deg": self.p90_view_incidence_deg,
        }


def _normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        raise ValueError("Cannot normalize a zero-length facade axis")
    normalized: np.ndarray = vector / norm
    return normalized


def _robust_sparse_plane_normal(
    sample: np.ndarray,
    reference_normal: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit the global elevation plane while tolerating architectural relief."""

    working = sample
    center = np.median(working, axis=0)
    eigenvalues: np.ndarray = np.ones(3, dtype=np.float64)
    eigenvectors: np.ndarray
    normal = reference_normal
    for retained_fraction in (1.0, 0.85, 0.75, 0.65):
        if retained_fraction < 1.0:
            distances = np.abs((sample - center) @ normal)
            working = sample[
                distances <= np.quantile(distances, retained_fraction)
            ]
        center = np.median(working, axis=0)
        covariance = np.cov((working - center).T)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        normal = _normalize(eigenvectors[:, 0])
        if float(normal @ reference_normal) < 0:
            normal = -normal
    planarity_ratio = float(eigenvalues[0] / max(eigenvalues[1], 1e-12))
    return normal, center, planarity_ratio


def estimate_facade_frame(
    points: object,
    cameras: Sequence[_CameraPose],
    *,
    ransac_iterations: int = 600,
) -> FacadeFrame:
    """Orient a facade from optimized optical axes and audit point planarity.

    Ornate facades contain arches, columns and deep openings; their largest
    RANSAC plane is often a moulding rather than the architectural elevation.
    Optimized camera optical axes provide the robust global viewing direction.
    Sparse points are retained to place the local origin and measure thickness.
    """

    xyz = np.asarray(points, dtype=np.float64)
    if xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) < 30:
        raise ValueError("At least 30 sparse 3D points are required for facade alignment")
    finite = np.isfinite(xyz).all(axis=1)
    xyz = xyz[finite]
    if len(xyz) < 30:
        raise ValueError("Too few finite sparse points for facade alignment")

    sample = xyz
    rng = np.random.default_rng(42)
    if len(sample) > 50_000:
        sample = sample[rng.choice(len(sample), 50_000, replace=False)]
    center = np.median(sample, axis=0)
    radial = np.linalg.norm(sample - center, axis=1)
    span = max(float(np.quantile(radial, 0.90)), 1e-6)
    threshold = max(span * 0.0125, 1e-6)

    camera_positions = np.asarray([camera.T for camera in cameras], dtype=np.float64)
    if len(camera_positions) < 2:
        raise ValueError("At least two registered cameras are required for facade alignment")
    image_up: list[np.ndarray] = []
    outward_axis_samples: list[np.ndarray] = []
    for camera in cameras:
        rotation = np.asarray(camera.R, dtype=np.float64)
        if rotation.shape == (3, 3):
            outward_axis_samples.append(_normalize(-rotation[:, 2]))
            image_up.append(_normalize(-rotation[:, 1]))
    if not outward_axis_samples:
        raise ValueError("Registered camera rotations do not define a facade normal")
    outward_axes = np.stack(outward_axis_samples)
    reference_normal = _normalize(np.median(outward_axes, axis=0))
    outward_axes = np.stack(
        [axis if float(axis @ reference_normal) >= 0 else -axis for axis in outward_axes]
    )
    optical_normal = _normalize(np.median(outward_axes, axis=0))
    if float(np.median((camera_positions - center) @ optical_normal)) < 0:
        optical_normal = -optical_normal
        outward_axes = -outward_axes

    sparse_normal, sparse_center, planarity_ratio = _robust_sparse_plane_normal(
        sample,
        optical_normal,
    )
    sparse_origin = np.median(sample, axis=0)
    sparse_origin += sparse_normal * (
        float(np.median(sample @ sparse_normal))
        - float(sparse_origin @ sparse_normal)
    )
    sparse_camera_side_ratio = float(
        np.mean(((camera_positions - sparse_origin) @ sparse_normal) > 0)
    )
    sparse_optical_angle_deg = float(
        np.degrees(
            np.arccos(
                np.clip(float(sparse_normal @ optical_normal), -1.0, 1.0)
            )
        )
    )
    use_sparse_plane = (
        planarity_ratio <= 0.35
        and sparse_camera_side_ratio >= 0.75
        and sparse_optical_angle_deg <= 70.0
    )
    if use_sparse_plane:
        normal = sparse_normal
        center = sparse_center
        orientation_source = "robust-sparse-elevation-plane"
    else:
        normal = optical_normal
        orientation_source = "optimized-camera-optical-axes"

    # Place the origin at the median facade depth while retaining a robust
    # component-wise centre in the other two directions.
    origin = np.median(sample, axis=0)
    median_depth = float(np.median(sample @ normal))
    origin += normal * (median_depth - float(origin @ normal))

    projected_ups: list[np.ndarray] = []
    for raw_up in image_up:
        projected = raw_up.copy()
        projected -= normal * float(projected @ normal)
        if np.linalg.norm(projected) > 1e-6:
            projected_ups.append(_normalize(projected))
    if not projected_ups:
        raise ValueError("Registered camera rotations do not define a facade vertical")
    reference_up = np.median(np.stack(projected_ups), axis=0)
    vertical = _normalize(reference_up - normal * float(reference_up @ normal))
    horizontal = _normalize(np.cross(vertical, normal))
    vertical = _normalize(np.cross(normal, horizontal))
    if float(vertical @ reference_up) < 0:
        horizontal = -horizontal
        vertical = -vertical

    distances = np.abs((sample - origin) @ normal)
    final_mask = distances <= threshold
    plane_rmse = float(np.sqrt(np.mean(distances[final_mask] ** 2)))
    view_incidence = np.degrees(
        np.arccos(np.clip(outward_axes @ normal, -1.0, 1.0))
    )
    return FacadeFrame(
        origin=origin,
        world_to_facade=np.stack([horizontal, vertical, normal]),
        inlier_ratio=float(final_mask.mean()),
        plane_rmse=plane_rmse,
        camera_side_ratio=float(np.mean(((camera_positions - origin) @ normal) > 0)),
        median_view_incidence_deg=float(np.median(view_incidence)),
        p90_view_incidence_deg=float(np.quantile(view_incidence, 0.90)),
        orientation_source=orientation_source,
    )
