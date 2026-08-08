"""Regression tests for the typed PCA camera-position boundary."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gaussian_ortho.pca_alignment import compute_pca_rotation


@dataclass
class CameraStub:
    T: np.ndarray


def test_pca_rotation_returns_an_orthonormal_up_frame() -> None:
    cameras = [
        CameraStub(np.asarray([x, y, 10.0], dtype=np.float64))
        for x, y in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]
    points = np.asarray(
        [[-1.0, -1.0, 0.0], [1.0, -1.0, 0.0], [1.0, 1.0, 0.0]],
        dtype=np.float64,
    )

    rotation, angle_deg = compute_pca_rotation(cameras, points)

    np.testing.assert_allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
    rotated_cameras = rotation @ np.stack([camera.T for camera in cameras]).T
    rotated_points = rotation @ points.T
    assert float(rotated_cameras[2].mean()) > float(rotated_points[2].mean())
    assert angle_deg == 0.0
