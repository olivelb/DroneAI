from __future__ import annotations

import numpy as np
import pytest

from gaussian_ortho.camera_footprint import (
    camera_assignment_for_ground_buffer,
    geographic_scene_frame,
)
from gaussian_ortho.colmap_loader import CameraInfo, PointCloud
from gaussian_ortho.partition import partition_scene, plan_partition_grid
from gaussian_ortho.scene_info import build_scene_info


IDENTITY_SIM3 = {
    "R": np.eye(3).tolist(),
    "scale": 1.0,
    "t": [0.0, 0.0, 0.0],
}
NADIR_CAMERA_ROTATION = np.diag([1.0, -1.0, -1.0]).astype(np.float32)


def _camera(
    *,
    uid: int = 1,
    center: tuple[float, float, float] = (0.0, 0.0, 100.0),
    rotation: np.ndarray = NADIR_CAMERA_ROTATION,
) -> CameraInfo:
    return CameraInfo(
        uid=uid,
        image_name=f"image-{uid}.jpg",
        width=1000,
        height=800,
        fx=500.0,
        fy=500.0,
        cx=500.0,
        cy=400.0,
        R=rotation,
        T=np.asarray(center, dtype=np.float32),
    )


def _flat_points() -> np.ndarray:
    x, y = np.meshgrid(np.linspace(0.0, 100.0, 20), np.linspace(0.0, 100.0, 20))
    return np.column_stack((x.ravel(), y.ravel(), np.zeros(x.size))).astype(
        np.float32
    )


def test_calibrated_ground_buffer_produces_native_crop_with_margin() -> None:
    points = _flat_points()
    frame = geographic_scene_frame(
        points,
        IDENTITY_SIM3,
        terrain_margin_m=0.0,
    )

    assignment = camera_assignment_for_ground_buffer(
        _camera(),
        frame,
        (-10.0, 10.0, -10.0, 10.0),
        crop_margin_pixels=20,
    )

    assert assignment is not None
    assert assignment.maximum_ground_overlap_m2 == pytest.approx(400.0)
    assert assignment.nadir_incidence_degrees == pytest.approx(0.0)
    assert assignment.crop.source_x == 430
    assert assignment.crop.source_y == 330
    assert assignment.crop.width == 140
    assert assignment.crop.height == 140


def test_upward_or_horizon_camera_is_not_visible() -> None:
    frame = geographic_scene_frame(
        _flat_points(),
        IDENTITY_SIM3,
        terrain_margin_m=0.0,
    )

    assignment = camera_assignment_for_ground_buffer(
        _camera(rotation=np.eye(3, dtype=np.float32)),
        frame,
        (-10.0, 10.0, -10.0, 10.0),
    )

    assert assignment is None


def test_partition_assigns_camera_by_footprint_not_camera_center() -> None:
    points = _flat_points()
    colors = np.ones_like(points)
    point_cloud = PointCloud(points=points, colors=colors, normals=np.zeros_like(points))
    cameras = [
        _camera(uid=index, center=(0.0, 50.0, 100.0))
        for index in range(1, 7)
    ]
    scene = build_scene_info(cameras, [], point_cloud)
    frame = geographic_scene_frame(
        points,
        IDENTITY_SIM3,
        terrain_margin_m=0.0,
    )

    cells = partition_scene(
        scene,
        m=1,
        n=2,
        overlap=0.0,
        min_cameras=5,
        model_to_ground_linear=frame.ground_linear,
        model_to_ground_offset=frame.ground_offset,
        geographic_frame=frame,
        crop_margin_pixels=16,
    )

    assert len(cells) == 2
    second_bounds, second_scene = cells[1]
    assert second_bounds.core_x_min == pytest.approx(50.0)
    assert len(second_scene.train_cameras) == len(cameras)
    assert set(second_scene.image_crops) == {
        camera.image_name for camera in cameras
    }
    assert all(
        crop.source_x > 0 and crop.width < crop.source_width
        for crop in second_scene.image_crops.values()
    )


def test_partition_grid_planner_follows_projected_scene_aspect() -> None:
    points = _flat_points()
    point_cloud = PointCloud(
        points=points,
        colors=np.ones_like(points),
        normals=np.zeros_like(points),
    )
    square_scene = build_scene_info([_camera()], [], point_cloud)
    assert plan_partition_grid(square_scene, 3) == (2, 2)

    wide_points = points.copy()
    wide_points[:, 0] *= 4.0
    wide_cloud = PointCloud(
        points=wide_points,
        colors=np.ones_like(wide_points),
        normals=np.zeros_like(wide_points),
    )
    wide_scene = build_scene_info([_camera()], [], wide_cloud)
    assert plan_partition_grid(wide_scene, 4) == (1, 4)
