from __future__ import annotations

import numpy as np
import pytest

from gaussian_ortho.coverage_quality import (
    SpatialCoveragePolicy,
    evaluate_spatial_coverage,
)


CAMERA_SQUARE = np.asarray(
    [
        [10.0, 10.0, 50.0],
        [90.0, 10.0, 50.0],
        [90.0, 90.0, 50.0],
        [10.0, 90.0, 50.0],
    ]
)
EXTENT = (0.0, 100.0, 0.0, 100.0)


def test_complete_projected_footprint_passes_spatial_gate() -> None:
    height = np.ones((160, 160), dtype=np.float32)

    report = evaluate_spatial_coverage(
        height,
        extent=EXTENT,
        camera_positions=CAMERA_SQUARE,
    )

    assert report["accepted"] is True
    assert report["footprint_source"] == "camera-center-convex-hull"
    assert report["valid_pixel_ratio"] == pytest.approx(1.0)
    assert report["worst_cell_ratio"] == pytest.approx(1.0)


def test_localized_hole_fails_even_when_global_coverage_is_high() -> None:
    height = np.ones((160, 160), dtype=np.float32)
    height[80:90, 80:90] = np.nan

    report = evaluate_spatial_coverage(
        height,
        extent=EXTENT,
        camera_positions=CAMERA_SQUARE,
    )

    assert report["valid_pixel_ratio"] > 0.95
    assert report["worst_cell_ratio"] == 0.0
    assert report["accepted"] is False
    failed = {check["name"] for check in report["checks"] if not check["passed"]}
    assert "worst_cell_ratio" in failed


def test_sparse_central_render_fails_projected_footprint_gate() -> None:
    height = np.full((160, 160), np.nan, dtype=np.float32)
    height[50:110, 50:110] = 20.0

    report = evaluate_spatial_coverage(
        height,
        extent=EXTENT,
        camera_positions=CAMERA_SQUARE,
    )

    assert report["accepted"] is False
    assert report["valid_pixel_ratio"] < 0.50
    assert report["covered_cells_ratio"] < 0.75


def test_disabled_gate_still_records_a_measured_rejection() -> None:
    height = np.full((160, 160), np.nan, dtype=np.float32)
    height[70:90, 70:90] = 20.0

    report = evaluate_spatial_coverage(
        height,
        extent=EXTENT,
        camera_positions=CAMERA_SQUARE,
        enforced=False,
    )

    assert report["accepted"] is False
    assert report["enforced"] is False
    assert report["status"] == "measured-rejected"


def test_collinear_corridor_falls_back_to_camera_cells() -> None:
    height = np.ones((160, 160), dtype=np.float32)
    cameras = np.asarray(
        [[10.0, 50.0, 20.0], [50.0, 50.0, 20.0], [90.0, 50.0, 20.0]]
    )

    report = evaluate_spatial_coverage(
        height,
        extent=EXTENT,
        camera_positions=cameras,
    )

    assert report["accepted"] is True
    assert report["footprint_source"] == "camera-cells-collinear"
    assert report["expected_cells"] == 3


def test_spatial_policy_rejects_invalid_thresholds() -> None:
    with pytest.raises(ValueError, match="grid_size"):
        SpatialCoveragePolicy(grid_size=2).validate()
    with pytest.raises(ValueError, match="minimum_valid_ratio"):
        SpatialCoveragePolicy(minimum_valid_ratio=1.1).validate()
