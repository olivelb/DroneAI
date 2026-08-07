import importlib
import inspect
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

image_preview = importlib.import_module("app4-dashboard.api.image_preview")
main = importlib.import_module("app4-dashboard.api.main")
messaging = importlib.import_module("app4-dashboard.api.messaging")
mission_state = importlib.import_module("app4-dashboard.api.mission_state")
map_support = importlib.import_module("app4-dashboard.api.map_support")


class FakeProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, *, key, value):
        self.messages.append((topic, key, json.loads(value)))

    def flush(self):
        return 0


def test_main_is_a_small_composition_root_with_all_public_routes():
    source_lines = inspect.getsource(main).splitlines()
    paths = set(main.app.openapi()["paths"])
    direct_paths = {route.path for route in main.app.routes if hasattr(route, "path")}

    assert len(source_lines) < 120
    assert "shared import storage" not in inspect.getsource(main)
    assert {
        "/",
        "/status/summary",
        "/mission",
        "/mission/cancel",
        "/mission/resume",
        "/mission/state",
        "/mission/parameters",
        "/pods",
        "/datasets",
        "/datasets/upload",
        "/datasets/upload-file",
        "/browse",
    } <= paths
    assert any(path.startswith("/preview/{s3_key}") for path in paths)
    assert any(path.startswith("/files/{s3_key}") for path in paths)
    assert "/maps/{vol_id}/metadata/{layer}" in paths
    assert "/maps/{vol_id}/tiles/{layer}/{z}/{x}/{y}.png" in paths
    assert "/maps/{vol_id}/vectors.geojson" in paths
    assert "/maps/{vol_id}/export/raster/{layer}" in paths
    assert "/maps/{vol_id}/export/vectors" in paths
    assert "/maps/{vol_id}/analyses" in paths
    assert "/maps/{vol_id}/analyses/{run_id}/retry" in paths
    assert "/maps/{vol_id}/analyses/{run_id}/vectors.geojson" in paths
    assert "/maps/{vol_id}/features" in paths
    assert "/maps/{vol_id}/features/{feature_id}" in paths
    assert "/maps/{vol_id}/search" in paths
    assert "/operations/outbox/dead" in paths
    assert "/ws/status" in direct_paths


def test_importing_the_api_does_not_create_a_kafka_producer():
    assert messaging._producer is None


def test_resume_events_are_unique_per_mission_attempt():
    first = messaging.build_resume_event(
        {"vol_id": "mission-1", "attempt": 1}
    )
    second = messaging.build_resume_event(
        {"vol_id": "mission-1", "attempt": 2}
    )

    assert first["attempt"] == 1
    assert second["attempt"] == 2
    assert first["event_id"] != second["event_id"]


def test_prepare_resume_increments_mission_attempt():
    mission = SimpleNamespace(
        service_states={"COLMAP": {"status": "error"}},
        params={"vol_id": "mission-1", "pipeline": "modern"},
        retry_count=4,
        status="error",
        current_step="ERROR",
        error_message="interrupted",
        updated_at=None,
    )

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return mission

    session = SimpleNamespace(query=lambda _model: Query())
    payload, response = mission_state.prepare_resume_in_session(
        session,
        "mission-1",
    )

    assert response["status"] == "success"
    assert payload["attempt"] == 5
    assert mission.retry_count == 5


def test_mission_status_policy_is_independent_from_http_and_kafka():
    assert mission_state.compute_overall_status({}) == "idle"
    assert (
        mission_state.compute_overall_status(
            {"COLMAP": {"status": "success"}}
        )
        == "processing"
    )
    assert (
        mission_state.compute_overall_status(
            {
                "COLMAP": {"status": "success"},
                "TILER": {"status": "success"},
                "IA": {"status": "success"},
            }
        )
        == "success"
    )
    assert (
        mission_state.compute_overall_status(
            {
                "COLMAP": {
                    "status": "success",
                    "details": {
                        "process": "map",
                        "terminal": True,
                    },
                }
            }
        )
        == "processing"
    )
    assert (
        mission_state.compute_overall_status(
            {
                "COLMAP": {"status": "success"},
                "IA": {"status": "error"},
            }
        )
        == "error"
    )
    assert (
        mission_state.compute_overall_status(
            {"COLMAP": {"status": "cancelled"}}
        )
        == "cancelled"
    )
    assert (
        mission_state.compute_overall_status(
            {
                "COLMAP": {
                    "status": "success",
                    "details": {
                        "process": "facade",
                        "terminal": True,
                    },
                }
            }
        )
        == "success"
    )

    mission = SimpleNamespace(
        service_states={"COLMAP": {"status": "error"}},
        params={"vol_id": "mission-1"},
    )
    resume = mission_state.build_colmap_resume_state(mission)
    assert resume["available"] is True
    assert resume["state"] == "resumable"

    cancelled_mission = SimpleNamespace(
        service_states={"COLMAP": {"status": "cancelled"}},
        params={"vol_id": "mission-1"},
    )
    cancelled_resume = mission_state.build_colmap_resume_state(cancelled_mission)
    assert cancelled_resume["available"] is True
    assert cancelled_resume["state"] == "cancelled"


def test_terminal_success_clears_an_earlier_transient_error(monkeypatch):
    mission = SimpleNamespace(
        id=1,
        service_states={
            "COLMAP": {"status": "success"},
            "TILER": {"status": "success"},
            "IA": {"status": "error"},
        },
        status="error",
        current_step="ERROR",
        progress=0,
        error_message="temporary S3 failure",
        resume_info=None,
        updated_at=None,
    )
    added = []
    session = SimpleNamespace(add=added.append)
    monkeypatch.setattr(
        mission_state,
        "get_or_create_mission",
        lambda _session, _vol_id: mission,
    )

    mission_state.apply_mission_state(
        session,
        {
            "vol_id": "mission-1",
            "service": "IA",
            "status": "success",
            "step": "DONE",
            "progress": 100,
        },
    )

    assert mission.status == "success"
    assert mission.error_message is None
    assert mission.current_step == "DONE"
    assert added


def test_image_preview_conversion_is_framework_independent():
    source = Image.new("I;16", (16, 8))
    pixels = [index * 16 for index in range(16 * 8)]
    source.putdata(pixels)
    raw = io.BytesIO()
    source.save(raw, format="TIFF")

    preview = image_preview.render_preview(
        raw.getvalue(),
        max_size=256,
        colormap="depth",
    )

    with Image.open(preview) as rendered:
        assert rendered.format == "PNG"
        assert rendered.mode == "RGB"
        assert rendered.size == (16, 8)


def test_image_preview_rejects_pixel_bomb_before_copy(monkeypatch):
    source = Image.new("L", (11, 10))
    raw = io.BytesIO()
    source.save(raw, format="PNG")
    monkeypatch.setattr(image_preview, "MAX_PREVIEW_PIXELS", 100)

    with pytest.raises(image_preview.PreviewTooLargeError):
        image_preview.render_preview(raw.getvalue())


def test_messaging_gateway_adds_contract_metadata():
    producer = FakeProducer()
    event = messaging.publish_new_mission(
        {
            "vol_id": "mission-1",
            "input_dataset": "datasets/mission-1",
        },
        kafka_producer=producer,
    )

    topic, key, published = producer.messages[0]
    assert topic == "vols-bruts"
    assert key == "mission-1"
    assert published == event
    assert published["schema_version"] == 1
    assert published["event_type"] == "mission"
    assert published["correlation_id"] == "mission-1"


def test_stored_feature_serialization_has_one_canonical_shape():
    feature = SimpleNamespace(
        feature_id="feature-1",
        properties={"source": "forged", "custom": 7},
        source="manual",
        name="Inspection",
        description=None,
        color="#123456",
        tags=["façade"],
        class_name=None,
        confidence=None,
        version=2,
        created_by="operator",
        updated_at=None,
    )

    result = map_support.stored_map_feature_geojson(
        feature,
        '{"type":"Point","coordinates":[1,2]}',
        "run-1",
    )

    assert result["geometry"]["coordinates"] == [1, 2]
    assert result["properties"]["source"] == "manual"
    assert result["properties"]["run_id"] == "run-1"
    assert result["properties"]["custom"] == 7
