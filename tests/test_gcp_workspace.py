from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from shared.tenancy import MissionObjectNamespace

from importlib import import_module

gcp_schemas = import_module("app4-dashboard.api.gcp_schemas")
gcp_workspace = import_module("app4-dashboard.api.gcp_workspace")


def test_safe_upload_name_strips_paths_and_allows_common_formats():
    assert gcp_workspace.safe_upload_name(r"C:\survey\markers.csv") == "markers.csv"
    assert gcp_workspace.safe_upload_name("gcp_list.txt") == "gcp_list.txt"
    assert gcp_workspace.safe_upload_name("points.geojson") == "points.geojson"
    assert gcp_workspace.safe_upload_name("markers.xml") == "markers.xml"
    assert gcp_workspace.safe_upload_name("points.kml") == "points.kml"


def test_safe_upload_name_rejects_unsupported_format():
    with pytest.raises(HTTPException) as error:
        gcp_workspace.safe_upload_name("markers.las")
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
    with pytest.raises(ValueError, match="finite"):
        gcp_workspace.validate_observation_pixels(float("nan"), 100, 320, 240)


def test_imported_pixels_are_marked_only_against_known_image_bounds():
    assert gcp_workspace.imported_observation_status(10, 20, 320, 240) == "marked"
    assert gcp_workspace.imported_observation_status(10, 20, None, None) == "candidate"
    with pytest.raises(ValueError, match="outside"):
        gcp_workspace.imported_observation_status(320, 20, 320, 240)
    with pytest.raises(ValueError, match="incomplete"):
        gcp_workspace.imported_observation_status(10, 20, 320, None)


@pytest.mark.parametrize(
    "observation",
    [
        SimpleNamespace(
            image_name="UNKNOWN.JPG",
            status="marked",
            pixel_x=10,
            pixel_y=20,
            image_width_px=None,
            image_height_px=None,
            image_s3_key=None,
        ),
        SimpleNamespace(
            image_name="DJI_1.JPG",
            status="marked",
            pixel_x=320,
            pixel_y=20,
            image_width_px=320,
            image_height_px=240,
            image_s3_key="datasets/survey/DJI_1.JPG",
        ),
        SimpleNamespace(
            image_name="DJI_1.JPG",
            status="marked",
            pixel_x=float("inf"),
            pixel_y=20,
            image_width_px=320,
            image_height_px=240,
            image_s3_key="datasets/survey/DJI_1.JPG",
        ),
        SimpleNamespace(
            image_name="MISSING.JPG",
            status="marked",
            pixel_x=10,
            pixel_y=20,
            image_width_px=320,
            image_height_px=240,
            image_s3_key=None,
        ),
        SimpleNamespace(
            image_name="DELETED.JPG",
            status="marked",
            pixel_x=10,
            pixel_y=20,
            image_width_px=320,
            image_height_px=240,
            image_s3_key="datasets/survey/DELETED.JPG",
        ),
    ],
)
def test_bundle_revalidates_every_marked_observation(monkeypatch, observation):
    monkeypatch.setattr(gcp_workspace.storage, "file_exists", lambda _key: False)
    point = SimpleNamespace(
        external_id="P1",
        source_x=1.25,
        source_y=44.5,
        source_z=210,
        role="adjustment",
        horizontal_accuracy_m=0.02,
        vertical_accuracy_m=0.03,
        image_accuracy_px=1,
        observations=[observation],
    )
    gcp_set = SimpleNamespace(
        set_id="set-1",
        source_sha256="a" * 64,
        source_crs="EPSG:4326",
        points=[point],
    )

    with pytest.raises(ValueError):
        gcp_workspace.materialize_gcp_bundle(gcp_set, "acme")


def test_bundle_accepts_only_revalidated_available_source_images(monkeypatch):
    def observation(index):
        return SimpleNamespace(
            image_name=f"DJI_{index:04d}.JPG",
            image_s3_key=f"datasets/survey/DJI_{index:04d}.JPG",
            status="marked",
            pixel_x=100 + index,
            pixel_y=200 + index,
            image_width_px=1200,
            image_height_px=800,
        )

    points = [
        SimpleNamespace(
            external_id=f"P{index}",
            source_x=1.25 + index,
            source_y=44.5 + index,
            source_z=210 + index,
            role="adjustment",
            horizontal_accuracy_m=0.02,
            vertical_accuracy_m=0.03,
            image_accuracy_px=1,
            observations=[observation(index * 2), observation(index * 2 + 1)],
        )
        for index in range(3)
    ]
    gcp_set = SimpleNamespace(
        set_id="set-1",
        source_sha256="a" * 64,
        source_crs="EPSG:4326",
        points=points,
    )
    monkeypatch.setattr(gcp_workspace.storage, "file_exists", lambda _key: True)

    def publish(path, *, organization_id):
        descriptor = gcp_workspace.bundle_blob(
            Path(path).read_bytes(),
            organization_id,
        )
        return gcp_workspace.storage.ContentAddressedUpload(
            key=descriptor["key"],
            size_bytes=descriptor["size"],
            checksum_sha256=descriptor["sha256"],
            reused=False,
            transferred_bytes=descriptor["size"],
        )

    monkeypatch.setattr(
        gcp_workspace.storage,
        "publish_content_addressed_file",
        publish,
    )

    bundle = gcp_workspace.materialize_gcp_bundle(gcp_set, "acme")

    assert bundle["schema_version"] == 2
    assert bundle["quality"]["marked_observations"] == 6


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
    namespace = MissionObjectNamespace.create("acme-survey", "mission")
    assert gcp_workspace.load_mission_image_positions(namespace) is None


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
