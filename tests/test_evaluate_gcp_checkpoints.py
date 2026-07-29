from pathlib import Path

import numpy as np
import pytest

from tools.evaluate_gcp_checkpoints import (
    intersect_rays,
    metric_projected_crs,
    parse_gcp_file,
    robust_inlier_mask,
    statistics,
)


def test_parse_odm_gcp_file(tmp_path: Path) -> None:
    gcp_file = tmp_path / "gcp_list.txt"
    gcp_file.write_text(
        "EPSG:4326\n"
        "16.1 47.2 500 100.5 200.25 IMG_0001.JPG P1\n"
        "16.1 47.2 500 110.5 210.25 IMG_0002.JPG P1\n",
        encoding="utf-8",
    )
    crs, observations = parse_gcp_file(gcp_file)
    assert crs == "EPSG:4326"
    assert len(observations) == 2
    assert observations[0].point_id == "P1"
    assert observations[0].pixel_xy == (100.5, 200.25)


def test_intersect_rays_recovers_point() -> None:
    expected = np.asarray([4.0, -2.0, 7.0])
    origins = [
        np.asarray([0.0, 0.0, 0.0]),
        np.asarray([10.0, 0.0, 0.0]),
        np.asarray([0.0, -10.0, 2.0]),
    ]
    directions = [expected - origin for origin in origins]
    actual, condition = intersect_rays(origins, directions)
    assert condition < 100.0
    assert actual == pytest.approx(expected, abs=1.0e-10)


def test_checkpoint_metrics_require_projected_metre_crs() -> None:
    assert metric_projected_crs("EPSG:32633").is_projected
    with pytest.raises(ValueError, match="must be projected"):
        metric_projected_crs("EPSG:4326")


def test_intersect_rays_rejects_parallel_geometry() -> None:
    with pytest.raises(ValueError, match="ill-conditioned"):
        intersect_rays(
            [np.zeros(3), np.ones(3)],
            [np.asarray([0.0, 0.0, 1.0]), np.asarray([0.0, 0.0, 1.0])],
        )


def test_robust_inlier_mask_rejects_large_outlier() -> None:
    assert robust_inlier_mask([0.6, 0.8, 0.7, 0.9, 50.0], 5.0) == [
        True,
        True,
        True,
        True,
        False,
    ]


def test_statistics_includes_rmse_and_p95() -> None:
    result = statistics([1.0, 2.0, 3.0, 4.0])
    assert result["count"] == 4
    assert result["median"] == 2.5
    assert result["rmse"] == pytest.approx(np.sqrt(7.5))
    assert result["p95"] == pytest.approx(3.85)
