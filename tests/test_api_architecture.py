import importlib
import inspect
import io
import json
from types import SimpleNamespace

from PIL import Image


image_preview = importlib.import_module("app4-dashboard.api.image_preview")
main = importlib.import_module("app4-dashboard.api.main")
messaging = importlib.import_module("app4-dashboard.api.messaging")
mission_state = importlib.import_module("app4-dashboard.api.mission_state")


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
    direct_paths = {
        route.path for route in main.app.routes if hasattr(route, "path")
    }

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
    assert "/ws/status" in direct_paths


def test_importing_the_api_does_not_create_a_kafka_producer():
    assert messaging._producer is None


def test_mission_status_policy_is_independent_from_http_and_kafka():
    assert mission_state.compute_overall_status({}) == "idle"
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
                "COLMAP": {"status": "success"},
                "IA": {"status": "error"},
            }
        )
        == "error"
    )

    mission = SimpleNamespace(
        service_states={"COLMAP": {"status": "error"}},
        params={"vol_id": "mission-1"},
    )
    resume = mission_state.build_colmap_resume_state(mission)
    assert resume["available"] is True
    assert resume["state"] == "resumable"


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
