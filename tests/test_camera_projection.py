from __future__ import annotations

import json

import pytest

from shared.camera_projection import (
    CameraProjection,
    CameraProjectionIndex,
    parse_camera_projection_index,
    project_world_point,
    rank_projected_image_candidates,
)


def _camera(name: str = "DJI_0001.JPG") -> CameraProjection:
    return CameraProjection(
        image_name=name,
        width=1000,
        height=800,
        model="PINHOLE",
        params=(500.0, 500.0, 500.0, 400.0),
        cam_from_world=(
            (1.0, 0.0, 0.0, 0.0),
            (0.0, 1.0, 0.0, 0.0),
            (0.0, 0.0, 1.0, 10.0),
        ),
    )


def test_projects_world_point_with_colmap_pinhole_conventions():
    assert project_world_point(_camera(), (0.0, 0.0, 0.0)) == pytest.approx((500.0, 400.0))
    assert project_world_point(_camera(), (2.0, 0.0, 0.0)) == pytest.approx((600.0, 400.0))


def test_ranks_only_visible_registered_images_and_excludes_reviewed():
    index = CameraProjectionIndex(
        crs="EPSG:4326",
        cameras=(_camera("center.JPG"), _camera("reviewed.JPG")),
    )

    candidates = rank_projected_image_candidates(
        longitude=0,
        latitude=0,
        altitude_m=0,
        camera_index=index,
        limit=10,
        existing_image_names={"reviewed.JPG"},
    )

    assert [item.image_name for item in candidates] == ["center.JPG"]
    assert candidates[0].pixel_x == pytest.approx(500)
    assert candidates[0].image_width_px == 1000


def test_rejects_camera_index_with_wrong_parameter_count():
    payload = {
        "schema_version": 1,
        "crs": "EPSG:2154",
        "images": [
            {
                "image_name": "bad.JPG",
                "width": 100,
                "height": 100,
                "model": "PINHOLE",
                "params": [50, 50, 50],
                "cam_from_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
            }
        ],
    }

    with pytest.raises(ValueError, match="expects 4 parameters"):
        parse_camera_projection_index(json.dumps(payload).encode())
