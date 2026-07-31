"""Regression tests for height-raster vertical referencing."""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gaussian_ortho.height_reference import (
    depth_buffer_to_height,
    georeference_height_map,
)


def test_depth_buffer_is_unprojected_and_empty_pixels_are_nodata() -> None:
    depth = np.array([[10.0, 25.5, 0.0, np.nan]], dtype=np.float32)

    height = depth_buffer_to_height(depth, camera_z=100.0)

    np.testing.assert_allclose(height[0, :2], [90.0, 74.5])
    assert np.isnan(height[0, 2:]).all()


def test_invalid_camera_height_is_rejected() -> None:
    with pytest.raises(ValueError, match="camera_z"):
        depth_buffer_to_height(np.ones((1, 1), dtype=np.float32), float("nan"))


def test_sim3_translation_is_added_to_local_model_z() -> None:
    height = np.array([[-48.0, -40.0]], dtype=np.float32)

    referenced, offset, source = georeference_height_map(
        height,
        sim3_aligned=True,
        geo_origin_z=510.0,
        colmap_to_meters=1.0,
        exif_altitude_available=False,
    )

    np.testing.assert_allclose(referenced, [[462.0, 470.0]])
    assert offset == 510.0
    assert source == "sim3"


def test_pca_height_is_scaled_then_anchored_by_exif_origin() -> None:
    height = np.array([[2.0, 3.0]], dtype=np.float32)

    referenced, offset, source = georeference_height_map(
        height,
        sim3_aligned=False,
        geo_origin_z=454.0,
        colmap_to_meters=3.0,
        exif_altitude_available=True,
    )

    np.testing.assert_allclose(referenced, [[460.0, 463.0]])
    assert offset == 454.0
    assert source == "exif"


def test_pca_without_altitude_keeps_explicit_local_z() -> None:
    height = np.array([[2.0, 3.0]], dtype=np.float32)

    referenced, offset, source = georeference_height_map(
        height,
        sim3_aligned=False,
        geo_origin_z=-999.0,
        colmap_to_meters=2.0,
        exif_altitude_available=False,
    )

    np.testing.assert_allclose(referenced, [[4.0, 6.0]])
    assert offset == 0.0
    assert source == "local"


@pytest.mark.parametrize("scale", [0.0, -1.0, float("nan")])
def test_invalid_scale_is_rejected(scale: float) -> None:
    with pytest.raises(ValueError, match="colmap_to_meters"):
        georeference_height_map(
            np.zeros((1, 1), dtype=np.float32),
            sim3_aligned=False,
            geo_origin_z=0.0,
            colmap_to_meters=scale,
            exif_altitude_available=False,
        )
