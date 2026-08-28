"""Real PostgreSQL/PostGIS boundaries for standalone analysis Stage Jobs."""

from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from shared import stage_execution
from shared.analysis_stages import create_analysis_stage
from shared.database import (
    AIAnalysisRun, MapFeature, Mission, MissionArtifact, MissionStageRun, Organization,
)
from shared.stage_execution import StageExecutionResult

pytestmark = pytest.mark.integration
routes = importlib.import_module("app4-dashboard.api.routers.map_analyses")
security = importlib.import_module("app4-dashboard.api.security")
map_support = importlib.import_module("app4-dashboard.api.map_support")


@pytest.fixture
def analysis_postgis(monkeypatch):
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope(**_kwargs):
        with factory.begin() as session:
            yield session

    for module in (routes, stage_execution):
        monkeypatch.setattr(module, "get_session", scope)
    tenant = "analysis-" + uuid4().hex[:12]
    with scope() as session:
        session.add(Organization(
            id=tenant, display_name=tenant, status="active", created_by="test", updated_by="test",
        ))
        session.flush()
        mission = Mission(
            vol_id="mission-analysis", organization_id=tenant, owner_subject="alice",
            workspace_prefix=f"organizations/{tenant}/missions/mission-analysis",
            status="success", params={"work_drive": "local"},
        )
        session.add(mission)
        session.flush()
        raster_stage = MissionStageRun(
            mission_id=mission.id, stage="rasterization", attempt=0,
            status="succeeded", idempotency_key=uuid4().hex + uuid4().hex,
        )
        session.add(raster_stage)
        session.flush()
        raster = MissionArtifact(
            mission_id=mission.id, stage_run_id=raster_stage.id,
            kind="raster_product_workspace", uri="s3://test/raster/manifest.json",
            checksum_sha256="a" * 64, size_bytes=1,
            artifact_metadata={"manifest_key": "raster/manifest.json", "ortho_file": "orthomosaic.tif"},
        )
        analysis = AIAnalysisRun(
            mission_id=mission.id, vol_id=mission.vol_id, name="Vehicles",
            ortho_s3_key=f"organizations/{tenant}/blobs/sha256/aa/raster",
            persist_results=True, created_by="alice",
        )
        session.add_all([raster, analysis])
        session.flush()
        stage = create_analysis_stage(session, mission, analysis, raster)
        stage.executor = "kubernetes-job"
        stage_id, analysis_id, run_id = stage.id, analysis.id, stage.run_id
    yield scope, tenant, stage_id, analysis_id, run_id
    engine.dispose()


def _result(tenant, analysis):
    return StageExecutionResult(
        kind="detection_workspace", uri=f"s3://test/{analysis.run_id}/manifest.json",
        checksum_sha256="b" * 64, size_bytes=200,
        metadata={
            "analysis_run_id": analysis.run_id, "manifest_key": f"{analysis.run_id}/manifest.json",
            "geojson_file": "detections.geojson",
            "geojson_object_key": f"organizations/{tenant}/blobs/sha256/bb/result",
            "model_manifest": {"backend": "yolo"}, "raster": {"tile_count": 1},
        },
        quality_metrics={"tile_count": 1, "geolocated_feature_count": 1},
        analysis_features=({
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [2.0, 48.0]},
            "properties": {"vol_id": analysis.vol_id, "class_name": "car", "confidence": 0.9},
        },),
    )


def test_analysis_publication_commits_postgis_features_and_artifact_together(analysis_postgis):
    scope, tenant, _stage_id, analysis_id, run_id = analysis_postgis
    with scope() as session:
        analysis = session.get(AIAnalysisRun, analysis_id)
    artifact_id = stage_execution.execute_one_shot_stage(
        "detection", lambda _context, _control: _result(tenant, analysis),
        run_id=run_id, heartbeat_interval_seconds=60,
    )
    with scope() as session:
        stored = session.get(AIAnalysisRun, analysis_id)
        assert (stored.status, stored.detection_count, stored.progress) == ("completed", 1, 100)
        feature = session.query(MapFeature).filter_by(analysis_run_id=analysis_id).one()
        assert session.scalar(select(func.ST_AsText(feature.geometry))) == "POINT(2 48)"
        artifact = session.query(MissionArtifact).filter_by(artifact_id=artifact_id).one()
        assert len(artifact.parent_edges) == 1
        assert map_support.resolve_detection_product(session, stored.mission) is None

    vectors = routes.analysis_vectors(
        analysis.vol_id, analysis.run_id, security.Principal("alice", "operator", tenant),
        owner_subject=None, bbox=None, limit=10,
    )
    assert vectors["features"][0]["properties"]["source"] == "ai"


def test_analysis_publication_failure_rolls_back_artifact_and_features(analysis_postgis):
    scope, tenant, _stage_id, analysis_id, run_id = analysis_postgis
    with scope() as session:
        analysis = session.get(AIAnalysisRun, analysis_id)
    result = _result(tenant, analysis)
    result.metadata["analysis_run_id"] = "wrong-analysis"
    with pytest.raises(ValueError, match="identity"):
        stage_execution.execute_one_shot_stage(
            "detection", lambda _context, _control: result,
            run_id=run_id, heartbeat_interval_seconds=60,
        )
    with scope() as session:
        assert session.get(AIAnalysisRun, analysis_id).status == "failed"
        assert session.query(MapFeature).filter_by(analysis_run_id=analysis_id).count() == 0
        assert session.query(MissionArtifact).filter_by(
            mission_id=analysis.mission_id, kind="detection_workspace",
        ).count() == 0


def test_database_rejects_analysis_stage_bound_to_another_mission(analysis_postgis):
    scope, tenant, stage_id, _analysis_id, _run_id = analysis_postgis
    with scope() as session:
        other = Mission(
            vol_id="other-mission", organization_id=tenant, owner_subject="alice",
            workspace_prefix=f"organizations/{tenant}/missions/other-mission",
        )
        session.add(other)
        session.flush()
        other_id = other.id
    with pytest.raises(IntegrityError, match="fk_stage_analysis_mission"):
        with scope() as session:
            session.get(MissionStageRun, stage_id).mission_id = other_id

def test_analysis_stage_publishes_with_non_owner_rls_role(analysis_postgis, monkeypatch):
    from sqlalchemy import text
    from sqlalchemy.orm.exc import NoResultFound

    scope, tenant, _stage_id, analysis_id, run_id = analysis_postgis
    role = "analysis_stage_" + uuid4().hex[:12]
    with scope() as session:
        session.execute(text(f"CREATE ROLE {role} NOLOGIN NOBYPASSRLS"))
        session.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        session.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
        session.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))
        analysis = session.get(AIAnalysisRun, analysis_id)

    @contextmanager
    def restricted_scope(*, organization_id=None):
        with scope() as session:
            session.execute(text(f"SET LOCAL ROLE {role}"))
            session.execute(
                text("SELECT set_config('droneai.organization_id', :tenant, true)"),
                {"tenant": organization_id or ""},
            )
            yield session

    monkeypatch.setattr(stage_execution, "get_session", restricted_scope)
    monkeypatch.setenv("DRONEAI_STAGE_RLS_REQUIRED", "true")
    monkeypatch.setenv("DRONEAI_ORGANIZATION_ID", tenant)
    try:
        with pytest.raises(NoResultFound):
            stage_execution.load_stage_execution_context(
                run_id, "detection", expected_organization_id="another-tenant",
            )
        stage_execution.execute_one_shot_stage(
            "detection", lambda _context, _control: _result(tenant, analysis),
            run_id=run_id, heartbeat_interval_seconds=60,
        )
        with restricted_scope(organization_id=tenant) as session:
            assert session.get(AIAnalysisRun, analysis_id).status == "completed"
            assert session.query(MapFeature).filter_by(analysis_run_id=analysis_id).count() == 1
        with restricted_scope(organization_id="another-tenant") as session:
            assert session.get(AIAnalysisRun, analysis_id) is None
            assert session.query(MapFeature).filter_by(analysis_run_id=analysis_id).count() == 0
    finally:
        with scope() as session:
            session.execute(text(f"DROP OWNED BY {role}"))
            session.execute(text(f"DROP ROLE {role}"))


def test_publication_and_cancel_serialize_without_foreign_key_deadlock(analysis_postgis, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from fastapi import HTTPException

    support = importlib.import_module("app4-dashboard.api.analysis_support")
    scope, tenant, _stage_id, analysis_id, run_id = analysis_postgis
    monkeypatch.setattr(support, "get_session", scope)
    with scope() as session:
        analysis = session.get(AIAnalysisRun, analysis_id)
    context = stage_execution.load_stage_execution_context(run_id, "detection")
    stage_locked, mission_locked = Event(), Event()
    original_reserve = stage_execution.reserve_stage_output_storage
    original_mission = support.get_mission

    def reserve_after_cancel_lock(*args, **kwargs):
        stage_locked.set()
        assert mission_locked.wait(10)
        return original_reserve(*args, **kwargs)

    def mission_for_cancel(*args, **kwargs):
        mission = original_mission(*args, **kwargs)
        if kwargs.get("for_update"):
            mission_locked.set()
        return mission

    monkeypatch.setattr(stage_execution, "reserve_stage_output_storage", reserve_after_cancel_lock)
    monkeypatch.setattr(support, "get_mission", mission_for_cancel)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(stage_execution._publish_result, context, _result(tenant, analysis))
        assert stage_locked.wait(10)
        cancellation = pool.submit(
            routes.cancel_analysis,
            security.Principal("alice", "operator", tenant), analysis.vol_id, analysis.run_id,
            owner_subject=None,
        )
        assert publication.result(timeout=15)
        with pytest.raises(HTTPException) as error:
            cancellation.result(timeout=15)
        assert error.value.status_code == 409
    with scope() as session:
        assert session.get(AIAnalysisRun, analysis_id).status == "completed"
        assert session.query(MapFeature).filter_by(analysis_run_id=analysis_id).count() == 1


def test_publication_and_mission_deletion_do_not_deadlock(analysis_postgis, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    missions = importlib.import_module("app4-dashboard.api.routers.missions")
    scope, tenant, _stage_id, analysis_id, run_id = analysis_postgis
    monkeypatch.setattr(missions, "get_session", scope)
    with scope() as session:
        analysis = session.get(AIAnalysisRun, analysis_id)
    context = stage_execution.load_stage_execution_context(run_id, "detection")
    stage_locked, mission_locked = Event(), Event()
    original_reserve = stage_execution.reserve_stage_output_storage
    original_cancel = missions.mark_cancellation_requested

    def reserve_after_mission_lock(*args, **kwargs):
        stage_locked.set()
        assert mission_locked.wait(10)
        return original_reserve(*args, **kwargs)

    def cancel_before_stage_lock(*args, **kwargs):
        result = original_cancel(*args, **kwargs)
        mission_locked.set()
        return result

    monkeypatch.setattr(stage_execution, "reserve_stage_output_storage", reserve_after_mission_lock)
    monkeypatch.setattr(missions, "mark_cancellation_requested", cancel_before_stage_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(stage_execution._publish_result, context, _result(tenant, analysis))
        assert stage_locked.wait(10)
        deletion = pool.submit(
            missions._delete_mission, analysis.vol_id,
            security.Principal("alice", "admin", tenant),
        )
        assert publication.result(timeout=15)
        assert deletion.result(timeout=15)["deletion_pending"] is True
    with scope() as session:
        mission = session.get(Mission, analysis.mission_id)
        assert (mission.status, mission.current_step) == ("cancelled", "DELETION_REQUESTED")
        assert session.query(MissionArtifact).filter_by(
            mission_id=mission.id, kind="detection_workspace",
        ).count() == 1
