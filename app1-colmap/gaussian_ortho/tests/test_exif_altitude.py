from types import SimpleNamespace

import numpy as np
import pytest

from gaussian_ortho import exif_altitude


def test_pair_sampling_is_deterministic_and_bounded():
    first = exif_altitude._sample_pair_indices(12, 1000)
    second = exif_altitude._sample_pair_indices(12, 1000)

    assert first.shape == (66, 2)
    assert np.array_equal(first, second)
    assert np.all((first >= 0) & (first < 12))


def test_exif_coordinates_accept_byte_references(tmp_path, monkeypatch):
    for name in ("north.jpg", "south.jpg", "ignored.txt"):
        (tmp_path / name).write_bytes(b"fixture")

    metadata = {
        "north.jpg": {
            "GPSLatitude": (45, 30, 0),
            "GPSLatitudeRef": b"N",
            "GPSLongitude": (1, 15, 0),
            "GPSLongitudeRef": b"E",
            "GPSAltitude": 120,
            "GPSAltitudeRef": b"\x00",
        },
        "south.jpg": {
            "GPSLatitude": (12, 0, 0),
            "GPSLatitudeRef": b"S",
            "GPSLongitude": (77, 0, 0),
            "GPSLongitudeRef": b"W",
            "GPSAltitude": 15,
            "GPSAltitudeRef": b"\x01",
        },
    }
    monkeypatch.setattr(
        exif_altitude,
        "_get_gps_info",
        lambda path: metadata.get(path.split("/")[-1]),
    )

    assert exif_altitude.extract_exif_gps(tmp_path) == {
        "north.jpg": (45.5, 1.25),
        "south.jpg": (-12.0, -77.0),
    }
    assert exif_altitude.extract_exif_altitudes(tmp_path) == {
        "north.jpg": 120.0,
        "south.jpg": -15.0,
    }


def test_geodesic_scale_recovers_relative_camera_baselines(monkeypatch):
    radius_m = 6_371_008.8
    longitude_step_deg = float(np.degrees(1.0 / radius_m))
    cameras = [
        SimpleNamespace(
            image_name=f"image-{index}.jpg",
            T=np.array([float(index), 0.0, 0.0]),
        )
        for index in range(12)
    ]
    monkeypatch.setattr(
        exif_altitude,
        "extract_exif_gps",
        lambda _path: {
            camera.image_name: (0.0, index * longitude_step_deg)
            for index, camera in enumerate(cameras)
        },
    )
    monkeypatch.setattr(
        exif_altitude,
        "extract_exif_altitudes",
        lambda _path: {camera.image_name: 100.0 for camera in cameras},
    )

    scale, source = exif_altitude.compute_colmap_scale_geodesic(
        cameras,
        "unused",
    )

    assert source == "relative-gps-baselines"
    assert scale == pytest.approx(1.0, rel=1e-6)


def test_geodesic_scale_falls_back_when_positions_are_insufficient(monkeypatch):
    cameras = [
        SimpleNamespace(image_name="one.jpg", T=np.zeros(3)),
    ]
    monkeypatch.setattr(
        exif_altitude,
        "extract_exif_gps",
        lambda _path: {"one.jpg": (0.0, 0.0)},
    )
    monkeypatch.setattr(
        exif_altitude,
        "extract_exif_altitudes",
        lambda _path: {"one.jpg": 10.0},
    )

    assert exif_altitude.compute_colmap_scale_geodesic(cameras, "unused") == (
        1.0,
        "model-units",
    )


def test_projected_origin_matches_gps_and_model_centroids(monkeypatch):
    cameras = [
        SimpleNamespace(image_name="first.jpg"),
        SimpleNamespace(image_name="second.jpg"),
    ]
    monkeypatch.setattr(
        exif_altitude,
        "extract_exif_gps",
        lambda _path: {
            "first.jpg": (0.0, 0.0),
            "second.jpg": (0.0, 0.008983152841195214),
        },
    )

    origin = exif_altitude.compute_projected_geo_origin(
        cameras,
        "unused",
        "EPSG:3857",
        np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]]),
        2.0,
        50.0,
    )

    assert origin == pytest.approx([490.0, 0.0, 50.0], abs=1e-6)
