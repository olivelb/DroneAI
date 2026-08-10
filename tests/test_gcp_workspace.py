from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from importlib import import_module

gcp_schemas = import_module("app4-dashboard.api.gcp_schemas")
gcp_workspace = import_module("app4-dashboard.api.gcp_workspace")


def test_safe_upload_name_strips_paths_and_allows_common_formats():
    assert gcp_workspace.safe_upload_name(r"C:\survey\markers.csv") == "markers.csv"
    assert gcp_workspace.safe_upload_name("gcp_list.txt") == "gcp_list.txt"
    assert gcp_workspace.safe_upload_name("points.geojson") == "points.geojson"


def test_safe_upload_name_rejects_unsupported_format():
    with pytest.raises(HTTPException) as error:
        gcp_workspace.safe_upload_name("markers.xml")
    assert error.value.status_code == 415


def test_read_bounded_object_closes_stream(monkeypatch):
    stream = BytesIO(b"payload")
    monkeypatch.setattr(
        gcp_workspace.storage,
        "get_object_stream",
        lambda _key: (stream, 7, "text/plain"),
    )

    assert gcp_workspace.read_bounded_object("key", 10) == b"payload"
    assert stream.closed


def test_reads_image_dimensions_incrementally_and_closes_stream(monkeypatch):
    raw = BytesIO()
    Image.new("RGB", (320, 240)).save(raw, format="JPEG")
    stream = BytesIO(raw.getvalue())
    monkeypatch.setattr(
        gcp_workspace.storage,
        "get_object_stream",
        lambda _key: (stream, len(raw.getvalue()), "image/jpeg"),
    )

    assert gcp_workspace.read_image_dimensions("datasets/image.jpg") == (320, 240)
    assert stream.closed


def test_rejects_gcp_pixels_outside_original_image():
    gcp_workspace.validate_observation_pixels(319.999, 239.999, 320, 240)
    with pytest.raises(ValueError, match="outside"):
        gcp_workspace.validate_observation_pixels(320, 100, 320, 240)


def test_observation_schema_requires_pixels_only_when_marked():
    marked = gcp_schemas.GcpObservationUpdate(status="marked", pixel_x=10, pixel_y=20, version=1)
    assert marked.pixel_x == 10
    with pytest.raises(ValueError, match="require pixel"):
        gcp_schemas.GcpObservationUpdate(status="marked", version=1)
    with pytest.raises(ValueError, match="only marked"):
        gcp_schemas.GcpObservationUpdate(status="skipped", pixel_x=10, pixel_y=20, version=1)
    with pytest.raises(ValueError):
        gcp_schemas.GcpObservationUpdate(status="marked", pixel_x=float("inf"), pixel_y=20, version=1)


def test_point_schema_requires_complete_manual_coordinates():
    with pytest.raises(ValueError, match="updated together"):
        gcp_schemas.GcpPointUpdate(longitude=1.2, version=1)
    update = gcp_schemas.GcpPointUpdate(
        longitude=1.2,
        latitude=44.5,
        altitude_m=205,
        role="checkpoint",
        version=3,
    )
    assert update.role == "checkpoint"


def test_load_mission_positions_returns_none_until_preflight(monkeypatch):
    monkeypatch.setattr(gcp_workspace.storage, "file_exists", lambda _key: False)
    assert gcp_workspace.load_mission_image_positions("mission") is None


def test_observation_json_exposes_optimistic_lock_version():
    payload = gcp_workspace.observation_json(
        SimpleNamespace(
            observation_id="obs-1",
            image_name="DJI_1.JPG",
            image_s3_key="datasets/demo/DJI_1.JPG",
            status="candidate",
            pixel_x=None,
            pixel_y=None,
            candidate_distance_m=12.5,
            candidate_method="exif-distance",
            projected_pixel_x=None,
            projected_pixel_y=None,
            image_width_px=None,
            image_height_px=None,
            image_longitude=1.2,
            image_latitude=44.5,
            version=2,
            updated_at=SimpleNamespace(isoformat=lambda: "2026-08-10T12:00:00Z"),
        )
    )
    assert payload["version"] == 2
    assert payload["candidate_distance_m"] == 12.5
