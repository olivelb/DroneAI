from pathlib import Path

import pytest

from tools.dataset_preflight import (
    build_geojson,
    build_report,
    discover_images,
    dms_to_decimal,
    haversine_distance_m,
    utm_epsg,
)


def _record(
    name: str,
    latitude: float | None,
    longitude: float | None,
    altitude: float | None = 100.0,
) -> dict:
    gps = None
    if latitude is not None and longitude is not None:
        gps = {
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude,
            "horizontal_error_m": None,
            "position_std_m": None,
            "vertical_reference": "unknown",
            "vertical_reference_source": "test",
        }
    return {
        "file": name,
        "size_bytes": 10,
        "readable": True,
        "gps": gps,
        "error": None,
        "width": 100,
        "height": 80,
        "format": "JPEG",
        "camera_make": "DJI",
        "camera_model": "FC3411",
        "captured_at": "2024:10:30 15:40:17",
        "focal_length_mm": 8.38,
    }


def test_dms_conversion_honors_hemisphere():
    assert dms_to_decimal((43, 1, 30), "N") == pytest.approx(43.025)
    assert dms_to_decimal((1, 7, 30), "W") == pytest.approx(-1.125)


@pytest.mark.parametrize(
    ("latitude", "longitude", "expected"),
    [
        (43.0, 1.0, "EPSG:32631"),
        (-33.0, 18.0, "EPSG:32734"),
        (0.0, 180.0, "EPSG:32660"),
    ],
)
def test_utm_epsg(latitude, longitude, expected):
    assert utm_epsg(latitude, longitude) == expected


def test_report_summarizes_gps_coverage_and_warns_for_standard_gnss():
    records = [
        _record("DJI_0001.JPG", 43.0, 1.0, 100.0),
        _record("DJI_0002.JPG", 43.0001, 1.0001, 102.0),
        _record("DJI_0003.JPG", None, None),
    ]

    report = build_report(records, dataset=Path("/data"), gps_quality="standard")

    assert report["summary"]["image_count"] == 3
    assert report["summary"]["gps_count"] == 2
    assert report["summary"]["gps_coverage_percent"] == pytest.approx(66.67)
    assert report["summary"]["recommended_projected_crs"] == "EPSG:3943"
    assert report["summary"]["projected_crs_selection"]["source"] == "france-cc9"
    assert report["summary"]["camera_models"] == {"FC3411": 3}
    assert len(report["warnings"]) == 3
    assert report["summary"]["height_product_reference"] == "unknown-or-mixed"


def test_report_preserves_ellipsoidal_rtk_vertical_reference():
    records = [
        _record("DJI_0001.JPG", 43.0, 1.0, 376.0),
        _record("DJI_0002.JPG", 43.0001, 1.0001, 376.2),
    ]
    for record in records:
        record["gps"].update(
            {
                "source": "dji_mrk",
                "vertical_reference": "ellipsoidal",
                "vertical_reference_source": "dji_mrk_ellh",
                "position_std_m": {
                    "north_m": 0.015,
                    "east_m": 0.018,
                    "vertical_m": 0.032,
                },
                "horizontal_error_m": 0.018,
            }
        )

    report = build_report(records, dataset=Path("/data"), gps_quality="rtk")

    assert report["summary"]["vertical_references"] == {"ellipsoidal": 2}
    assert report["summary"]["height_product_reference"] == "ellipsoidal"
    assert report["summary"]["vertical_error_m"]["median"] == pytest.approx(
        0.032
    )
    assert not any(
        "orthometric" in warning for warning in report["warnings"]
    )


def test_geojson_contains_flight_path_and_camera_points():
    records = [
        _record("DJI_0001.JPG", 43.0, 1.0),
        _record("DJI_0002.JPG", 43.0001, 1.0001),
    ]

    geojson = build_geojson(records)

    assert geojson["type"] == "FeatureCollection"
    assert geojson["features"][0]["geometry"]["type"] == "LineString"
    assert len(geojson["features"]) == 3


def test_haversine_distance_is_zero_for_same_position():
    assert haversine_distance_m((43.0, 1.0), (43.0, 1.0)) == pytest.approx(0.0)


def test_report_can_force_historical_utm_policy():
    records = [
        _record("DJI_0001.JPG", 43.0, 1.0),
        _record("DJI_0002.JPG", 43.0001, 1.0001),
    ]

    report = build_report(
        records,
        dataset=Path("/data"),
        projected_crs_mode="utm",
    )

    assert report["summary"]["recommended_projected_crs"] == "EPSG:32631"
    assert report["summary"]["projected_crs_selection"]["source"] == "utm"


def test_discover_images_limits_preflight_to_requested_flights(tmp_path):
    first = tmp_path / "flight-one"
    second = tmp_path / "flight-two"
    ignored = tmp_path / "unrelated-flight"
    for directory in (first, second, ignored):
        directory.mkdir()
        (directory / "DJI_0001.JPG").touch()

    images = discover_images(tmp_path, ["flight-one", "flight-two"])

    assert [path.relative_to(tmp_path).as_posix() for path in images] == [
        "flight-one/DJI_0001.JPG",
        "flight-two/DJI_0001.JPG",
    ]


def test_discover_images_rejects_prefix_outside_dataset(tmp_path):
    with pytest.raises(ValueError, match="escapes the dataset"):
        discover_images(tmp_path, ["../outside"])
