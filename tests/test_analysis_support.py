from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared import stage_execution
from shared.analysis_stages import sync_analysis_stage
from shared.database import (
    AIAnalysisRun, Dataset, DatasetUploadSession, DetectionShardReceipt, Mission, MissionArtifact,
    MissionArtifactParent, MissionStageRun, Organization, OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.stage_execution import StageExecutionCancelled, StageExecutionResult

routes = importlib.import_module("app4-dashboard.api.routers.map_analyses")
support = importlib.import_module("app4-dashboard.api.analysis_support")
map_support = importlib.import_module("app4-dashboard.api.map_support")
projection = importlib.import_module("app4-dashboard.api.stage_projection")
security = importlib.import_module("app4-dashboard.api.security")
orchestrator = importlib.import_module("app4-dashboard.api.stage_orchestrator")


@pytest.fixture
def analysis_sessions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (
        Organization, OrganizationSaasPolicy, OrganizationUsageEvent,
        DatasetUploadSession, Dataset, Mission, AIAnalysisRun, MissionStageRun,
        MissionArtifact, MissionArtifactParent, DetectionShardReceipt,
    ):
        model.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope(**_kwargs):
        with factory.begin() as session:
            yield session

    for module in (routes, support, stage_execution, orchestrator):
        monkeypatch.setattr(module, "get_session", scope)
    monkeypatch.setattr(routes, "stage_jobs_enabled", lambda: True)
    with scope() as session:
        session.add(Organization(id="tenant-a", display_name="Tenant A", status="active", created_by="test", updated_by="test"))
        mission = Mission(
            vol_id="mission-analysis", organization_id="tenant-a", owner_subject="alice",
            workspace_prefix="organizations/tenant-a/missions/mission-analysis",
            status="success", params={"phases": ["rasterization"], "work_drive": "local"},
        )
        session.add(mission)
        session.flush()
        raster_stage = MissionStageRun(
            mission_id=mission.id, stage="rasterization", attempt=0,
            status="succeeded", progress=100, idempotency_key="a" * 64,
        )
        session.add(raster_stage)
        session.flush()
        session.add(MissionArtifact(
            artifact_id="raster-current", mission_id=mission.id, stage_run_id=raster_stage.id,
            kind="raster_product_workspace", uri="s3://test/raster/manifest.json",
            checksum_sha256="b" * 64, size_bytes=1,
            artifact_metadata={"manifest_key": "raster/manifest.json", "ortho_file": "orthomosaic.tif"},
        ))
    monkeypatch.setattr(routes, "resolve_raster_product", lambda *_args: map_support.RasterProductObject(
        key="organizations/tenant-a/blobs/sha256/aa/raster", default_colormap="",
        artifact_id="raster-current",
    ))
    yield scope
    engine.dispose()


def _launch(backend="yolo"):
    return routes.create_analysis(
        "mission-analysis",
        routes.AnalysisCreate(name="Vehicles", backend=backend, persist_results=False),
        security.Principal("alice", "operator", "tenant-a"), owner_subject=None,
    )


def _stage(scope, run_id):
    with scope() as session:
        analysis = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).one()
        stage = session.query(MissionStageRun).filter(
            MissionStageRun.analysis_run_id == analysis.id,
        ).order_by(MissionStageRun.attempt.desc()).first()
        return stage


@pytest.mark.parametrize(("backend", "resource"), [("yolo", "gpu-standard"), ("sam3", "gpu-geometry")])
def test_analysis_queues_exact_raster_and_backend_resource(analysis_sessions, backend, resource):
    launched = _launch(backend)
    stage = _stage(analysis_sessions, launched["run_id"])
    assert stage.status == "queued"
    assert stage.resource_class == resource
    assert stage.upstream_artifact_ids == ["raster-current"]
    assert stage.parameters["ai"]["backend"] == backend
    assert stage.parameters["work_drive"] == "local"
    assert stage.analysis_run_id is not None


def test_distinct_analyses_have_independent_attempts(analysis_sessions):
    first = _stage(analysis_sessions, _launch()["run_id"])
    second = _stage(analysis_sessions, _launch()["run_id"])
    assert first.attempt != second.attempt
    assert first.idempotency_key != second.idempotency_key
    with analysis_sessions() as session:
        mission = session.query(Mission).one()
        stages = session.query(MissionStageRun).all()
        projected = projection.project_stage_mission(mission, stages)
        assert projected["overall_status"] == "success"


def test_completed_stage_publishes_analysis_without_replacing_pipeline(analysis_sessions):
    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    with analysis_sessions() as session:
        session.get(MissionStageRun, stage.id).executor = "kubernetes-job"

    def handler(context, control):
        assert context.analysis["run_id"] == launched["run_id"]
        control.report_progress(1, 2)
        with analysis_sessions() as session:
            analysis = session.query(AIAnalysisRun).one()
            assert (analysis.status, analysis.tiles_completed, analysis.total_tiles) == ("running", 1, 2)
        return StageExecutionResult(
            kind="detection_workspace", uri="s3://test/analysis/manifest.json",
            checksum_sha256="c" * 64, size_bytes=10, analysis_features=(),
            metadata={
                "analysis_run_id": launched["run_id"], "geojson_file": "detections.geojson",
                "geojson_object_key": "organizations/tenant-a/blobs/sha256/cc/result",
                "model_manifest": {"backend": "yolo"}, "raster": {"tile_count": 2},
            },
            quality_metrics={"tile_count": 2, "geolocated_feature_count": 0},
        )
    stage_execution.execute_one_shot_stage("detection", handler, run_id=stage.run_id, heartbeat_interval_seconds=60)
    with analysis_sessions() as session:
        analysis = session.query(AIAnalysisRun).one()
        assert (analysis.status, analysis.tiles_completed, analysis.progress) == ("completed", 2, 100)
        assert map_support.resolve_detection_product(session, session.query(Mission).one()) is None


def test_cancel_isolated_analysis_prevents_late_publication(analysis_sessions):
    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    with analysis_sessions() as session:
        session.get(MissionStageRun, stage.id).executor = "kubernetes-job"
    context = stage_execution.load_stage_execution_context(stage.run_id, "detection")
    result = routes.cancel_analysis(
        security.Principal("alice", "operator", "tenant-a"),
        "mission-analysis", launched["run_id"], owner_subject=None,
    )
    assert result["status"] == "cancelled"
    with pytest.raises(StageExecutionCancelled):
        stage_execution._publish_result(context, StageExecutionResult(
            kind="detection_workspace", uri="s3://test/result", checksum_sha256="d" * 64, size_bytes=1,
        ))
    with analysis_sessions() as session:
        assert session.query(Mission).one().status == "success"
        assert session.query(MissionArtifact).count() == 1


def test_retry_pins_original_raster_and_late_failure_cannot_clobber_new_attempt(analysis_sessions):
    launched = _launch()
    old = _stage(analysis_sessions, launched["run_id"])
    stage_execution._mark_terminal(old.run_id, "tenant-a", "failed", "worker crashed")
    result = routes.retry_analysis(
        "mission-analysis", launched["run_id"],
        security.Principal("alice", "operator", "tenant-a"), owner_subject=None,
    )
    new = _stage(analysis_sessions, launched["run_id"])
    assert result["retry_count"] == 1
    assert new.run_id != old.run_id
    assert new.upstream_artifact_ids == old.upstream_artifact_ids
    stage_execution._mark_terminal(old.run_id, "tenant-a", "failed", "late worker")
    with analysis_sessions() as session:
        analysis = session.query(AIAnalysisRun).one()
        assert analysis.status == "queued"
        assert analysis.error_message is None


def test_dispatch_exhaustion_is_visible_on_analysis(analysis_sessions):
    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    with analysis_sessions() as session:
        record = session.get(MissionStageRun, stage.id)
        record.dispatch_attempts = 3
    orchestrator._record_dispatch_error(stage.run_id, RuntimeError("unavailable"), 3)
    with analysis_sessions() as session:
        analysis = session.query(AIAnalysisRun).one()
        assert analysis.status == "failed"
        assert "dispatch failed" in analysis.error_message


def test_cross_tenant_analysis_access_is_hidden(analysis_sessions):
    launched = _launch()
    with pytest.raises(HTTPException) as error:
        routes.cancel_analysis(
            security.Principal("alice", "operator", "tenant-b"),
            "mission-analysis", launched["run_id"], owner_subject=None,
        )
    assert error.value.status_code == 404


def test_analysis_creation_fails_closed_without_stage_jobs(analysis_sessions, monkeypatch):
    monkeypatch.setattr(routes, "stage_jobs_enabled", lambda: False)
    with pytest.raises(HTTPException) as error:
        _launch()
    assert error.value.status_code == 503

def test_cancelled_mission_cannot_queue_an_analysis(analysis_sessions):
    with analysis_sessions() as session:
        session.query(Mission).one().status = "cancelled"
    with pytest.raises(HTTPException) as error:
        _launch()
    assert error.value.status_code == 409


def test_pipeline_status_events_cannot_mutate_an_analysis_stage(analysis_sessions):
    from shared.phase_dag import project_status_to_stage_run

    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    with analysis_sessions() as session:
        project_status_to_stage_run(
            session, session.query(Mission).one(),
            service="IA", step="DETECTION", event_status="processing", progress=50,
            error_message=None, stage_run_id=stage.run_id,
        )
    with analysis_sessions() as session:
        assert session.get(MissionStageRun, stage.id).status == "queued"
        assert session.get(MissionStageRun, stage.id).progress == 0


def test_nonpersisted_analysis_reads_its_own_versioned_result(analysis_sessions, monkeypatch):
    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    with analysis_sessions() as session:
        record = session.get(MissionStageRun, stage.id)
        record.status = "succeeded"
        sync_analysis_stage(session, record)
        session.add(MissionArtifact(
            mission_id=record.mission_id, stage_run_id=record.id,
            kind="detection_workspace", uri="s3://test/analysis/manifest.json",
            checksum_sha256="e" * 64, size_bytes=10,
            artifact_metadata={
                "analysis_run_id": launched["run_id"],
                "manifest_key": "analysis/manifest.json", "geojson_file": "detections.geojson",
            },
        ))
    reads = []

    def workspace_files(key, checksum, tenant):
        reads.append((key, checksum, tenant))
        return {"detections.geojson": "organizations/tenant-a/blobs/sha256/ee/result"}

    monkeypatch.setattr(map_support, "_workspace_object_keys", workspace_files)
    monkeypatch.setattr(map_support, "detection_product_features", lambda product, vol_id, bounds, limit, props: (
        [{"type": "Feature", "properties": props, "key": product.key}], False,
    ))
    with analysis_sessions() as session:
        mission = session.query(Mission).one()
        run = session.query(AIAnalysisRun).one()
        features, truncated = map_support.analysis_artifact_features(session, mission, run, None, 10)
        assert features[0]["properties"]["run_id"] == launched["run_id"]
        assert features[0]["properties"]["source"] == "ai"
        assert not truncated
    assert reads == [("analysis/manifest.json", "e" * 64, "tenant-a")]

def test_sharded_analysis_progress_uses_only_receipts_for_its_exact_plan(analysis_sessions):
    from shared.detection_sharding import build_detection_shard_plan

    launched = _launch()
    stage = _stage(analysis_sessions, launched["run_id"])
    plan = build_detection_shard_plan(600, 500, 256, 64, tiles_per_shard=3)
    with analysis_sessions() as session:
        record = session.get(MissionStageRun, stage.id)
        record.status = "running"
        record.provenance = {"detection_shard_plan": plan.descriptor()}
        for checksum, shard_index in ((plan.checksum_sha256, 0), ("f" * 64, 1)):
            session.add(DetectionShardReceipt(
                stage_run_id=stage.id, plan_checksum_sha256=checksum,
                shard_index=shard_index, shard_count=plan.shard_count,
                tile_count=plan.shard(shard_index).tile_count,
                result_key="organizations/tenant-a/blobs/sha256/aa/result",
                result_checksum_sha256="a" * 64, result_size_bytes=1,
            ))
        session.flush()
        sync_analysis_stage(session, record)
        analysis = session.query(AIAnalysisRun).one()
        assert analysis.total_tiles == plan.tile_count
        assert analysis.tiles_completed == plan.shard(0).tile_count
        assert 0 < analysis.progress < 100


@pytest.mark.parametrize(
    ("mission_status", "current_step"),
    [
        ("success", "DELETION_REQUESTED"),
        ("error", "MANUAL_DELETION_FAILED"),
        ("deleting", None),
        ("deletion_failed", None),
        ("cancelled", "CANCELLATION_REQUESTED"),
    ],
)
def test_analysis_rejects_mission_deletion_states(
    analysis_sessions, mission_status, current_step,
):
    with analysis_sessions() as session:
        mission = session.query(Mission).one()
        mission.status = mission_status
        mission.current_step = current_step
    with pytest.raises(HTTPException) as error:
        _launch()
    assert error.value.status_code == 409
    with analysis_sessions() as session:
        assert session.query(AIAnalysisRun).count() == 0
