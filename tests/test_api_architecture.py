import importlib
import inspect
import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import Mission, OutboxEvent

image_preview = importlib.import_module("app4-dashboard.api.image_preview")
main = importlib.import_module("app4-dashboard.api.main")
messaging = importlib.import_module("app4-dashboard.api.messaging")
mission_state = importlib.import_module("app4-dashboard.api.mission_state")
map_support = importlib.import_module("app4-dashboard.api.map_support")
analysis_routes = importlib.import_module("app4-dashboard.api.routers.map_analyses")
export_routes = importlib.import_module("app4-dashboard.api.routers.map_exports")
feature_routes = importlib.import_module("app4-dashboard.api.routers.map_features")
mission_routes = importlib.import_module("app4-dashboard.api.routers.missions")
dataset_routes = importlib.import_module("app4-dashboard.api.routers.datasets")
operation_routes = importlib.import_module("app4-dashboard.api.routers.operations")


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
        "/browse",
    } <= paths
    assert "/datasets/upload-file" not in paths
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
    first = messaging.build_resume_event({"vol_id": "mission-1", "attempt": 1})
    second = messaging.build_resume_event({"vol_id": "mission-1", "attempt": 2})

    assert first["attempt"] == 1
    assert second["attempt"] == 2
    assert first["event_id"] != second["event_id"]


def test_new_mission_event_is_deterministic_for_one_mission_id():
    payload = {
        "vol_id": "mission-1",
        "input_dataset": "datasets/mission-1",
    }

    first = messaging.build_new_mission_event(payload)
    second = messaging.build_new_mission_event(payload)

    assert first["event_id"] == second["event_id"]


def test_start_mission_rejects_an_existing_id(monkeypatch):
    existing = SimpleNamespace(vol_id="mission-1")

    class Query:
        def filter(self, *_args):
            return self

        def first(self):
            return existing

    session = SimpleNamespace(query=lambda _model: Query())

    @contextmanager
    def session_scope():
        yield session

    monkeypatch.setattr(mission_routes, "get_session", session_scope)
    monkeypatch.setattr(
        mission_routes,
        "enqueue_outbox",
        lambda *_args, **_kwargs: pytest.fail("duplicate mission must not enqueue an event"),
    )
    params = SimpleNamespace(
        vol_id="mission-1",
        pipeline="modern",
        input_dataset="datasets/mission-1",
        model_dump=lambda: {
            "vol_id": "mission-1",
            "pipeline": "modern",
            "input_dataset": "datasets/mission-1",
        },
    )

    with pytest.raises(HTTPException) as error:
        mission_routes.start_mission(params)

    assert error.value.status_code == 409
    assert "already exists" in error.value.detail


def test_cancel_mission_persists_state_and_outbox_atomically(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope():
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    with session_scope() as session:
        session.add(Mission(vol_id="mission-1", status="processing", retry_count=2))
    monkeypatch.setattr(mission_routes, "get_session", session_scope)

    response = mission_routes.cancel_mission("mission-1")

    assert response["status"] == "success"
    with session_scope() as session:
        mission = session.query(Mission).one()
        outbox = session.query(OutboxEvent).one()
        assert mission.status == "cancelled"
        assert mission.current_step == "CANCELLATION_REQUESTED"
        assert outbox.event_type == "control"
        assert outbox.payload["attempt"] == 2


def test_sync_io_handlers_are_threadpool_eligible():
    assert not inspect.iscoroutinefunction(mission_routes.start_mission)
    assert not inspect.iscoroutinefunction(mission_routes.resume_mission)
    assert not inspect.iscoroutinefunction(mission_routes.cancel_mission)
    assert not inspect.iscoroutinefunction(dataset_routes.upload_dataset_batch)
    assert not inspect.iscoroutinefunction(analysis_routes.create_analysis)
    assert not inspect.iscoroutinefunction(analysis_routes.retry_analysis)
    assert not inspect.iscoroutinefunction(analysis_routes.cancel_analysis)
    assert not inspect.iscoroutinefunction(analysis_routes.analysis_vectors)
    assert not inspect.iscoroutinefunction(export_routes.export_raster)
    assert not inspect.iscoroutinefunction(export_routes.export_vectors)
    assert not inspect.iscoroutinefunction(feature_routes.create_map_feature)
    assert not inspect.iscoroutinefunction(feature_routes.update_map_feature)
    assert not inspect.iscoroutinefunction(feature_routes.delete_map_feature)
    assert not inspect.iscoroutinefunction(feature_routes.search_map_features)


def test_manual_feature_update_rejects_a_stale_version(monkeypatch):
    feature = SimpleNamespace(version=3)

    class Query:
        def filter(self, *_criteria):
            return self

        def with_for_update(self):
            return self

        def first(self):
            return feature

    session = SimpleNamespace(
        query=lambda _model: Query(),
        flush=lambda: pytest.fail("stale updates must not be flushed"),
    )

    @contextmanager
    def session_scope():
        yield session

    monkeypatch.setattr(feature_routes, "get_session", session_scope)
    request = SimpleNamespace(version=2)

    with pytest.raises(HTTPException) as error:
        feature_routes.update_map_feature(
            "mission-1",
            "feature-1",
            request,
            None,
        )

    assert error.value.status_code == 409
    assert error.value.detail == {
        "message": "Feature was changed by another user",
        "current_version": 3,
    }


def test_map_search_aggregates_feature_bounds():
    features = [
        {
            "geometry": {"type": "Point", "coordinates": [2.0, 5.0]},
        },
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-1.0, 3.0], [4.0, 3.0], [4.0, 8.0], [-1.0, 3.0]],
                ],
            },
        },
    ]

    assert feature_routes._aggregate_bounds(features) == [-1.0, 3.0, 4.0, 8.0]


def test_raster_export_stream_closes_its_object_body():
    body = io.BytesIO(b"abcdef")

    chunks = list(export_routes._stream_object(body, chunk_size=2))

    assert chunks == [b"ab", b"cd", b"ef"]
    assert body.closed


def test_object_store_analysis_vectors_apply_bounds_and_limit(monkeypatch):
    inside = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
        "properties": {},
    }
    second_inside = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [0.75, 0.75]},
        "properties": {},
    }
    skipped_tile = SimpleNamespace(
        result_s3_key="outside.json",
        bounds_wgs84=[10.0, 10.0, 11.0, 11.0],
    )
    selected_tile = SimpleNamespace(
        result_s3_key="inside.json",
        bounds_wgs84=[0.0, 0.0, 1.0, 1.0],
    )
    loaded = []

    def load_payload(key):
        loaded.append(key)
        return {"features": [inside, second_inside]}

    monkeypatch.setattr(analysis_routes, "load_json_object", load_payload)

    features, truncated = analysis_routes._object_store_features(
        [skipped_tile, selected_tile],
        (0.0, 0.0, 1.0, 1.0),
        1,
    )

    assert features == [inside]
    assert truncated is True
    assert loaded == ["inside.json"]


def test_dead_outbox_replay_resets_delivery_state(monkeypatch):
    record = SimpleNamespace(
        id=17,
        event_id="event-17",
        event_type="mission",
        topic="vols-bruts",
        attempts=5,
        last_error="broker unavailable",
        status="dead",
        available_at=None,
        dead_at=datetime(2026, 8, 8, tzinfo=UTC),
        locked_at=datetime(2026, 8, 8, tzinfo=UTC),
        locked_by="worker-1",
    )

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def limit(self, _value):
            return self

        def all(self):
            return [record]

        def with_for_update(self):
            return self

        def first(self):
            return record

    session = SimpleNamespace(query=lambda _model: Query())

    @contextmanager
    def session_scope():
        yield session

    monkeypatch.setattr(operation_routes, "get_session", session_scope)

    dead_events = operation_routes.list_dead_outbox_events(limit=10)
    response = operation_routes.replay_dead_outbox_event(record.id)

    assert dead_events[0]["event_id"] == "event-17"
    assert response == {"status": "queued", "id": 17}
    assert record.status == "pending"
    assert record.attempts == 0
    assert record.available_at.tzinfo is UTC
    assert record.dead_at is None
    assert record.locked_at is None
    assert record.locked_by is None


def test_frontend_uses_the_server_validated_batch_upload():
    source = (Path(__file__).resolve().parents[1] / "app4-dashboard" / "frontend" / "app" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    upload_source = source.split(
        "export const uploadDataset = async",
        1,
    )[1].split("const encodeS3Key", 1)[0]

    assert "/datasets/upload?" in upload_source
    assert "/datasets/upload-file" not in upload_source
    assert 'formData.append("files"' in upload_source


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
    assert mission_state.compute_overall_status({"COLMAP": {"status": "success"}}) == "processing"
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
    assert mission_state.compute_overall_status({"COLMAP": {"status": "cancelled"}}) == "cancelled"
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


@pytest.mark.parametrize("vol_id", [None, 42, ""])
def test_mission_state_rejects_invalid_event_identifier(vol_id):
    with pytest.raises(ValueError, match="status event has no vol_id"):
        mission_state.apply_mission_state(
            SimpleNamespace(),
            {"vol_id": vol_id},
        )


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


def test_stored_vector_payload_must_be_a_json_object(monkeypatch):
    stream = io.BytesIO(b"[]")
    monkeypatch.setattr(
        map_support.storage,
        "get_object_stream",
        lambda _key: (stream, 2, "application/json"),
    )

    with pytest.raises(HTTPException) as error:
        map_support.load_json_object("missions/mission-1/vectors.json")

    assert error.value.status_code == 422
    assert stream.closed
