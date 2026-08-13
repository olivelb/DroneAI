import importlib
import inspect
import io
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionStageRun,
    OutboxEvent,
)

image_preview = importlib.import_module("app4-dashboard.api.image_preview")
main = importlib.import_module("app4-dashboard.api.main")
messaging = importlib.import_module("app4-dashboard.api.messaging")
mission_state = importlib.import_module("app4-dashboard.api.mission_state")
map_support = importlib.import_module("app4-dashboard.api.map_support")
analysis_routes = importlib.import_module("app4-dashboard.api.routers.map_analyses")
export_routes = importlib.import_module("app4-dashboard.api.routers.map_exports")
feature_routes = importlib.import_module("app4-dashboard.api.routers.map_features")
feature_mutation_routes = importlib.import_module(
    "app4-dashboard.api.routers.map_feature_mutations"
)
mission_routes = importlib.import_module("app4-dashboard.api.routers.missions")
dataset_routes = importlib.import_module("app4-dashboard.api.routers.datasets")
operation_routes = importlib.import_module("app4-dashboard.api.routers.operations")
TEST_PRINCIPAL = SimpleNamespace(
    subject="test-operator",
    role="admin",
    organization_id="legacy-unassigned",
)


class FakeProducer:
    def __init__(self):
        self.messages = []

    def produce(self, topic, *, key, value, on_delivery=None):
        self.messages.append((topic, key, json.loads(value)))
        if on_delivery is not None:
            self.delivery_callback = on_delivery

    def poll(self, _timeout):
        callback = getattr(self, "delivery_callback", None)
        if callback is not None:
            self.delivery_callback = None
            callback(None, None)
        return 0

    def flush(self):
        return 0


def test_main_is_a_small_composition_root_with_all_public_routes():
    source_lines = inspect.getsource(main).splitlines()
    paths = set(main.app.openapi()["paths"])
    direct_paths = {route.path for route in main.app.routes if hasattr(route, "path")}

    assert len(source_lines) < 125
    assert "shared import storage" not in inspect.getsource(main)
    assert {
        "/",
        "/status/summary",
        "/mission",
        "/mission/cancel",
        "/mission/resume",
        "/mission/state",
        "/mission/parameters",
        "/missions",
        "/missions/{vol_id}",
        "/missions/{vol_id}/stages/{stage}/runs",
        "/missions/{vol_id}/stages/runs/{run_id}/artifacts",
        "/pods",
        "/datasets",
        "/datasets/upload",
        "/datasets/upload-sessions",
        "/browse",
        "/auth/session",
        "/auth/bootstrap",
        "/auth/organization",
        "/auth/members",
        "/auth/credentials",
        "/auth/audit-events",
        "/auth/access-audit-events",
        "/auth/invitations",
        "/auth/recovery-tokens",
        "/auth/capabilities/redeem",
        "/platform/me",
        "/platform/organizations",
        "/platform/organizations/{organization_id}/status",
        "/platform/credentials",
        "/platform/audit-events",
        "/operations/organization/capacity",
        "/operations/organization/usage-events",
    } <= paths
    assert {"/live", "/ready"} <= direct_paths
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
    assert "/maps/{vol_id}/features/bulk" in paths
    assert "/maps/{vol_id}/features/{feature_id}/audit" in paths
    assert "/maps/{vol_id}/styles/{layer}" in paths
    assert "/maps/{vol_id}/styles/{layer}/{style_id}" in paths
    assert "/maps/{vol_id}/search" in paths
    assert {
        "/maps/{vol_id}/gcps",
        "/maps/{vol_id}/gcps/import",
        "/maps/{vol_id}/gcps/{set_id}",
        "/maps/{vol_id}/gcps/{set_id}/bundle",
        "/maps/{vol_id}/gcps/{set_id}/candidates/refresh",
        "/maps/{vol_id}/gcps/{set_id}/audit",
        "/maps/{vol_id}/gcps/points/{point_id}",
        "/maps/{vol_id}/gcps/observations/{observation_id}",
    } <= paths
    assert "/operations/outbox/dead" in paths
    assert "/ws/status" in direct_paths


def test_every_map_route_exposes_the_explicit_admin_owner_scope():
    schema = main.app.openapi()
    for path, operations in schema["paths"].items():
        if not path.startswith("/maps/"):
            continue
        for operation in operations.values():
            parameter_names = {
                parameter["name"] for parameter in operation.get("parameters", [])
            }
            assert "owner_subject" in parameter_names, path


def test_gcp_import_and_refresh_keep_distinct_transport_contracts():
    schema = main.app.openapi()
    import_operation = schema["paths"]["/maps/{vol_id}/gcps/import"]["post"]
    refresh_operation = schema["paths"][
        "/maps/{vol_id}/gcps/{set_id}/candidates/refresh"
    ]["post"]

    assert import_operation["requestBody"]["required"] is True
    refresh_parameters = {
        (parameter["name"], parameter["in"])
        for parameter in refresh_operation["parameters"]
    }
    assert ("candidate_radius_m", "query") in refresh_parameters
    assert ("max_candidates", "query") in refresh_parameters


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


def test_mission_event_identity_is_isolated_by_organization():
    first = messaging.build_new_mission_event(
        {"vol_id": "mission-1", "organization_id": "tenant-a"}
    )
    second = messaging.build_new_mission_event(
        {"vol_id": "mission-1", "organization_id": "tenant-b"}
    )

    assert first["event_id"] != second["event_id"]
    assert first["correlation_id"] == "tenant-a:mission-1"


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
        mission_routes._start_mission(params, TEST_PRINCIPAL)

    assert error.value.status_code == 409
    assert "already exists" in error.value.detail


def test_start_mission_persists_profile_overrides_and_model_identity(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DatasetUploadSession.__table__.create(engine)
    Dataset.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
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

    monkeypatch.setattr(mission_routes, "get_session", session_scope)
    with session_scope() as session:
        session.add(
            Dataset(
                name="profile-mission",
                owner_subject="test-operator",
                prefix="datasets/profile-mission",
                status="ready",
                manifest_s3_key="datasets/profile-mission/dataset-manifest.json",
                file_count=1,
                image_count=1,
                total_bytes=1,
                ready_at=datetime.now(UTC),
            )
        )
    params = mission_routes.MissionParams(
        vol_id="profile-mission-001",
        input_dataset="datasets/profile-mission",
        quality_profile="high-quality-v1",
        ai_model_variant="yolo26n",
        colmap_params={"gs_iterations": "35000"},
    )

    assert mission_routes._start_mission(params, TEST_PRINCIPAL)["status"] == "success"

    with session_scope() as session:
        mission = session.query(Mission).one()
        outbox = session.query(OutboxEvent).one()
        assert mission.params["quality_profile"] == "high-quality-v1"
        assert mission.owner_subject == "test-operator"
        assert mission.params["quality_profile_version"] == 1
        assert mission.params["quality_profile_overrides"] == {
            "gs_iterations": "35000"
        }
        assert mission.params["colmap_params"]["gs_cap_max"] == "5000000"
        assert mission.params["colmap_params"]["gs_iterations"] == "35000"
        assert mission.params["ai_model_manifest"]["id"] == "yolo26n"
        assert outbox.payload["ai_model_manifest"]["artifact_sha256"]


def test_parameter_catalog_exposes_profiles_and_model_capabilities(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv(
        "DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED", raising=False
    )
    response = mission_routes.mission_parameters()

    assert response["quality_profile_default"] == "normal-v3"
    assert [profile["id"] for profile in response["quality_profiles"]] == [
        "fast-v1",
        "normal-v3",
        "high-quality-v2",
    ]
    assert len(response["yolo_models"]) == 8
    assert all(model["selectable_classes"] for model in response["yolo_models"])
    assert response["sam3"] == {
        "model_id": "facebook/sam3",
        "model_revision": "3c879f39826c281e95690f02c7821c4de09afae7",
        "processor_target_size": 1008,
        "maximum_source_tile_size": 1024,
        "inference_batch_size": 1,
        "minimum_vram_gib": 12,
    }


def test_parameter_catalog_can_expose_hq_v3_for_controlled_qualification(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED", "true"
    )

    response = mission_routes.mission_parameters()

    assert [profile["id"] for profile in response["quality_profiles"]] == [
        "fast-v1",
        "normal-v3",
        "high-quality-v2",
        "fast-v2",
        "normal-v4",
        "high-quality-v3",
        "high-quality-v4",
    ]
    candidate = next(
        profile
        for profile in response["quality_profiles"]
        if profile["id"] == "high-quality-v4"
    )
    assert candidate["parameters"]["gs_resident_partitioning"] is True
    assert candidate["parameters"]["gs_target_gaussian_spacing_pixels"] == "3.6"
    assert candidate["parameters"]["gs_initial_scale_policy"] == "projected-knn"


def test_parameter_catalog_rejects_ambiguous_candidate_flag(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv(
        "DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED", "yes"
    )

    with pytest.raises(
        RuntimeError,
        match="DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED",
    ):
        mission_routes.mission_parameters()


def test_sam3_mission_payload_does_not_persist_an_irrelevant_yolo_model():
    params = mission_routes.MissionParams(
        vol_id="sam3-mission-001",
        input_dataset="datasets/sam3-mission",
        ai_backend="sam3",
        ai_model_variant="yolo26l",
        sam_prompt="building",
        classes=["building"],
    )

    payload = mission_routes._mission_payload(params)

    assert payload["ai_backend"] == "sam3"
    assert "ai_model_variant" not in payload


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
        session.add(
            Mission(
                vol_id="mission-1",
                owner_subject="test-operator",
                status="processing",
                retry_count=2,
            )
        )
    monkeypatch.setattr(mission_routes, "get_session", session_scope)

    response = mission_routes._cancel_mission("mission-1", TEST_PRINCIPAL)

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
    assert not inspect.iscoroutinefunction(feature_mutation_routes.create_map_feature)
    assert not inspect.iscoroutinefunction(feature_mutation_routes.update_map_feature)
    assert not inspect.iscoroutinefunction(feature_mutation_routes.delete_map_feature)
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

    monkeypatch.setattr(feature_mutation_routes, "get_session", session_scope)
    request = SimpleNamespace(version=2)

    with pytest.raises(HTTPException) as error:
        feature_mutation_routes.update_map_feature(
            "mission-1",
            "feature-1",
            request,
            TEST_PRINCIPAL,
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

    monkeypatch.setattr(map_support, "load_json_object", load_payload)

    features, truncated = analysis_routes._object_store_features(
        [skipped_tile, selected_tile],
        (0.0, 0.0, 1.0, 1.0),
        1,
        vol_id="mission-1",
        tiling_metadata={},
    )

    assert features == [inside]
    assert truncated is True
    assert loaded == ["inside.json"]


def test_object_store_analysis_vectors_read_versioned_tile_artifacts(monkeypatch):
    tile = SimpleNamespace(
        result_s3_key="tile-result.json",
        bounds_wgs84=None,
    )
    monkeypatch.setattr(
        map_support,
        "load_json_object",
        lambda _key: {
            "schema_version": 1,
            "raw_detections": [
                {
                    "geo_lon": 2.25,
                    "geo_lat": 48.75,
                    "class_name": "truck",
                    "confidence": 0.9,
                    "tile_index": 4,
                }
            ],
        },
    )

    features, truncated = analysis_routes._object_store_features(
        [tile],
        None,
        10,
        vol_id="mission-1",
        tiling_metadata={},
    )

    assert truncated is False
    assert features[0]["geometry"] == {
        "type": "Point",
        "coordinates": [2.25, 48.75],
    }
    assert features[0]["properties"]["tile_index"] == 4


def test_dead_outbox_replay_is_tenant_scoped_and_resets_delivery_state(monkeypatch):
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

    filters = []

    class Query:
        def filter(self, *criteria):
            filters.extend(criteria)
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

    dead_events = operation_routes.list_dead_outbox_events(TEST_PRINCIPAL, limit=10)
    response = operation_routes.replay_dead_outbox_event(record.id, TEST_PRINCIPAL)

    assert dead_events[0]["event_id"] == "event-17"
    assert response == {"status": "queued", "id": 17}
    assert len(filters) == 4
    assert record.status == "pending"
    assert record.attempts == 0
    assert record.available_at.tzinfo is UTC
    assert record.dead_at is None
    assert record.locked_at is None
    assert record.locked_by is None


def test_outbox_delivery_status_is_tenant_scoped_and_payload_free(monkeypatch):
    record = SimpleNamespace(
        id=18,
        organization_id="tenant-a",
        event_id="event-18",
        event_type="control",
        topic="pipeline-control",
        message_key="tenant-a:mission-1",
        status="published",
        attempts=1,
        published_at=datetime(2026, 8, 12, tzinfo=UTC),
        last_error=None,
    )
    filters = []

    class Query:
        def filter(self, *criteria):
            filters.extend(criteria)
            return self

        def order_by(self, *_args):
            return self

        def limit(self, _value):
            return self

        def all(self):
            return [record]

    @contextmanager
    def session_scope():
        yield SimpleNamespace(query=lambda _model: Query())

    monkeypatch.setattr(operation_routes, "get_session", session_scope)
    principal = SimpleNamespace(organization_id="tenant-a")

    response = operation_routes.list_outbox_delivery_status(
        principal,
        delivery_status="published",
        limit=10,
    )

    assert len(filters) == 2
    assert response == [
        {
            "id": 18,
            "event_id": "event-18",
            "event_type": "control",
            "topic": "pipeline-control",
            "message_key": "tenant-a:mission-1",
            "status": "published",
            "attempts": 1,
            "published_at": datetime(2026, 8, 12, tzinfo=UTC),
            "last_error": None,
        }
    ]
    assert "payload" not in response[0]

    with pytest.raises(HTTPException) as invalid_status:
        operation_routes.list_outbox_delivery_status(
            principal,
            delivery_status="unknown",
            limit=10,
        )
    assert invalid_status.value.status_code == 422


def test_frontend_uses_direct_presigned_multipart_upload():
    api_root = Path(__file__).resolve().parents[1] / "app4-dashboard" / "frontend" / "app" / "lib"
    source = (api_root / "api-upload.ts").read_text(encoding="utf-8")
    barrel_source = (api_root / "api.ts").read_text(encoding="utf-8")
    upload_source = source.split("export const uploadDataset = async", 1)[1]

    assert 'export { uploadDataset } from "./api-upload"' in barrel_source
    assert '"/datasets/upload-sessions"' in upload_source
    assert "signed.url" in source
    assert 'credentials: "omit"' in source
    assert "/parts/${partNumber}" in source
    assert "/complete" in upload_source
    assert "/datasets/upload?" not in upload_source
    assert "/datasets/upload-file" not in upload_source
    assert 'formData.append("files"' not in upload_source


def test_prepare_resume_increments_mission_attempt():
    mission = SimpleNamespace(
        vol_id="mission-1",
        organization_id="legacy-unassigned",
        workspace_prefix="missions/mission-1",
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
        "test-operator",
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


def test_processing_retry_clears_a_recovered_service_error(monkeypatch):
    mission = SimpleNamespace(
        id=1,
        service_states={"COLMAP": {"status": "error"}},
        status="error",
        current_step="ERROR",
        progress=0,
        error_message="temporary worker error",
        resume_info=None,
        updated_at=None,
    )
    monkeypatch.setattr(
        mission_state,
        "get_or_create_mission",
        lambda _session, _vol_id: mission,
    )

    mission_state.apply_mission_state(
        SimpleNamespace(add=lambda _record: None),
        {
            "vol_id": "mission-1",
            "service": "COLMAP",
            "status": "processing",
            "step": "GAUSS",
            "progress": 68,
        },
    )

    assert mission.status == "processing"
    assert mission.error_message is None


def test_delayed_progress_does_not_resurrect_a_cancelled_mission(monkeypatch):
    mission = SimpleNamespace(
        id=1,
        service_states={"COLMAP": {"status": "cancelled"}},
        status="cancelled",
        current_step="CANCELLED",
        progress=0,
        error_message=None,
        resume_info=None,
        updated_at=None,
    )
    monkeypatch.setattr(
        mission_state,
        "get_or_create_mission",
        lambda _session, _vol_id: mission,
    )

    mission_state.apply_mission_state(
        SimpleNamespace(add=lambda _record: None),
        {
            "vol_id": "mission-1",
            "service": "COLMAP",
            "status": "processing",
            "step": "GAUSS",
            "progress": 68,
        },
    )

    assert mission.status == "cancelled"


def test_stale_heartbeat_is_monitoring_metadata_not_pipeline_failure(monkeypatch):
    monkeypatch.setattr(mission_state, "MISSION_PROCESSING_STALE_SECONDS", 120)
    mission = SimpleNamespace(
        id=1,
        vol_id="mission-1",
        organization_id="legacy-unassigned",
        workspace_prefix="missions/mission-1",
        owner_subject="test-operator",
        service_states={"COLMAP": {"status": "processing", "step": "GAUSS"}},
        status="processing",
        current_step="GAUSS",
        progress=68,
        retry_count=0,
        resume_info=None,
        params={"vol_id": "mission-1"},
        error_message=None,
        created_at=datetime.now(UTC) - timedelta(minutes=20),
        updated_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    serialized = mission_state.serialize_mission(mission)

    assert serialized["overall_status"] == "processing"
    assert serialized["is_stale"] is True
    assert serialized["last_event_age_seconds"] >= 600


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
    assert key == "legacy-unassigned:mission-1"
    assert published == event
    assert published["schema_version"] == 1
    assert published["event_type"] == "mission"
    assert published["organization_id"] == "legacy-unassigned"
    assert published["correlation_id"] == "legacy-unassigned:mission-1"


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
