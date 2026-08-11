import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from shared.gcp_control import (
    _robust_observation_mask,
    assess_gcp_alignment_quality,
    build_image_lookup,
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


def test_image_lookup_accepts_unambiguous_extensionless_metashape_labels() -> None:
    image = SimpleNamespace(
        image_id=1,
        name="survey/IMG_0001.JPG",
    )

    lookup = build_image_lookup(SimpleNamespace(images={1: image}))

    assert lookup["survey/IMG_0001.JPG"] is image
    assert lookup["IMG_0001.JPG"] is image
    assert lookup["IMG_0001"] is image


def test_image_lookup_rejects_ambiguous_extensionless_labels() -> None:
    jpeg = SimpleNamespace(image_id=1, name="a/IMG_0001.JPG")
    tiff = SimpleNamespace(image_id=2, name="b/IMG_0001.tif")

    lookup = build_image_lookup(SimpleNamespace(images={1: jpeg, 2: tiff}))

    assert "IMG_0001" not in lookup
    assert lookup["IMG_0001.JPG"] is jpeg
    assert lookup["IMG_0001.tif"] is tiff


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


def _quality_report(checkpoints: list[tuple[float, float, float]]) -> dict:
    points = [
        {
            "role": "adjustment",
            "surveyed_xyz": xyz,
            "horizontal_error_m": 0.01,
            "vertical_error_m": 0.01,
            "euclidean_error_m": 0.015,
            "normalized_error_norm_sigma": 0.5,
        }
        for xyz in ([0, 0, 0], [20, 0, 0], [0, 20, 0])
    ]
    points.extend(
        {
            "role": "checkpoint",
            "surveyed_xyz": [5 + index, 5, 0],
            "horizontal_error_m": horizontal,
            "vertical_error_m": vertical,
            "euclidean_error_m": math.hypot(horizontal, vertical),
            "normalized_error_norm_sigma": normalized,
        }
        for index, (horizontal, vertical, normalized) in enumerate(checkpoints)
    )
    return {"points": points}


def test_gcp_quality_gate_distinguishes_fit_from_independent_accuracy() -> None:
    unverified = assess_gcp_alignment_quality(
        _quality_report([]), minimum_adjustment_baseline_m=5.0
    )
    assert unverified["accepted"] is True
    assert unverified["status"] == "accepted-unverified"

    rejected = assess_gcp_alignment_quality(
        _quality_report([(0.25, 0.05, 2.0)]),
        maximum_checkpoint_horizontal_rmse_m=0.10,
    )
    assert rejected["accepted"] is False
    assert rejected["status"] == "rejected"


def test_gcp_quality_gate_can_require_independent_checkpoints() -> None:
    quality = assess_gcp_alignment_quality(
        _quality_report([]), require_checkpoints=True
    )
    assert quality["accepted"] is False
    assert quality["verification"] == "unverified-no-checkpoints"
