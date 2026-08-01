from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from shared.gcp_control import (
    _robust_observation_mask,
    intersect_rays,
    parse_gcp_accuracy_file,
    parse_gcp_file,
    prepare_gcp_assets,
)
from shared.geo_alignment import estimate_weighted_sim3


def test_parse_gcp_and_per_point_accuracy(tmp_path: Path) -> None:
    gcp_path = tmp_path / "gcp_list.txt"
    gcp_path.write_text(
        "EPSG:32633\n"
        "610000 5277000 500 100 200 IMG_1.JPG G1\n"
        "610000 5277000 500 110 210 IMG_2.JPG G1\n",
        encoding="utf-8",
    )
    accuracy_path = tmp_path / "gcp_accuracy.csv"
    accuracy_path.write_text(
        "point_id,horizontal_accuracy_m,vertical_accuracy_m,image_accuracy_px,role\n"
        "G1,0.012,0.025,0.4,checkpoint\n",
        encoding="utf-8",
    )

    crs, observations = parse_gcp_file(gcp_path)
    accuracy = parse_gcp_accuracy_file(accuracy_path)

    assert crs == "EPSG:32633"
    assert len(observations) == 2
    assert accuracy["G1"].horizontal_m == pytest.approx(0.012)
    assert accuracy["G1"].vertical_m == pytest.approx(0.025)
    assert accuracy["G1"].image_px == pytest.approx(0.4)
    assert accuracy["G1"].role == "checkpoint"


def test_accuracy_file_rejects_non_positive_uncertainty(tmp_path: Path) -> None:
    path = tmp_path / "gcp_accuracy.csv"
    path.write_text(
        "point_id,horizontal_accuracy_m,vertical_accuracy_m,image_accuracy_px\n"
        "G1,0,0.02,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="positive and finite"):
        parse_gcp_accuracy_file(path)


def test_prepare_gcp_assets_copies_and_removes_stale_files(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset" / "control"
    workspace = tmp_path / "workspace"
    dataset.mkdir(parents=True)
    (dataset / "gcp_list.txt").write_text("EPSG:32633\n", encoding="utf-8")
    (dataset / "gcp_accuracy.csv").write_text(
        "point_id,horizontal_accuracy_m,vertical_accuracy_m,image_accuracy_px\n"
        "G1,0.01,0.02,0.5\n",
        encoding="utf-8",
    )

    first = prepare_gcp_assets(dataset.parent, workspace)
    second = prepare_gcp_assets(dataset.parent, workspace)
    assert first["changed"] is True
    assert second["changed"] is False
    assert Path(first["gcp_path"]).is_file()
    assert Path(first["accuracy_path"]).is_file()

    (dataset / "gcp_list.txt").unlink()
    (dataset / "gcp_accuracy.csv").unlink()
    removed = prepare_gcp_assets(dataset.parent, workspace)
    assert removed == {"gcp_path": None, "accuracy_path": None, "changed": True}


def test_weighted_sim3_downweights_unreliable_control() -> None:
    source = np.asarray(
        [
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [0.0, 12.0, 0.0],
            [8.0, 9.0, 2.0],
            [4.0, 5.0, 1.0],
        ]
    )
    expected_rotation = Rotation.from_euler("xyz", [1.0, -2.0, 7.0], degrees=True)
    expected_scale = 1.002
    expected_translation = np.asarray([610000.0, 5277000.0, 512.0])
    target = (
        expected_scale * expected_rotation.as_matrix() @ source.T
    ).T + expected_translation
    target[-1] += np.asarray([0.8, -0.6, 0.5])
    sigma = np.full_like(source, 0.01)
    sigma[-1] = 2.0

    result = estimate_weighted_sim3(source, target, sigma)

    assert result["scale"] == pytest.approx(expected_scale, abs=2.0e-4)
    assert np.asarray(result["t"]) == pytest.approx(
        expected_translation,
        abs=0.01,
    )
    assert np.asarray(result["R"]) == pytest.approx(
        expected_rotation.as_matrix(),
        abs=2.0e-3,
    )


def test_weighted_ray_intersection_returns_finite_covariance() -> None:
    expected = np.asarray([3.0, -2.0, 8.0])
    origins = [
        np.asarray([0.0, 0.0, 0.0]),
        np.asarray([10.0, 0.0, 0.0]),
        np.asarray([0.0, -10.0, 1.0]),
    ]
    directions = [expected - origin for origin in origins]

    actual, covariance, condition = intersect_rays(
        origins,
        directions,
        [0.01, 0.01, 0.02],
    )

    assert actual == pytest.approx(expected, abs=1.0e-10)
    assert np.linalg.eigvalsh(covariance).min() > 0
    assert condition < 100.0


def test_reprojection_mask_never_restores_non_finite_observations() -> None:
    assert _robust_observation_mask([float("inf"), 0.3, 0.4], 1.0) == [
        False,
        True,
        True,
    ]
    assert _robust_observation_mask([float("inf"), 0.3], 1.0) == [
        False,
        True,
    ]
