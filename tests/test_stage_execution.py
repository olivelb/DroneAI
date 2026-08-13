
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    Organization,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.organization_saas import StorageQuotaExceeded
from shared import stage_execution
from shared.stage_execution import (
    StageExecutionCancelled,
    StageQualityGateRejected,
    StageExecutionResult,
    execute_one_shot_stage,
    execute_stage_subtask,
)


@pytest.fixture
def execution_sessions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Organization.__table__.create(engine)
    OrganizationSaasPolicy.__table__.create(engine)
    OrganizationUsageEvent.__table__.create(engine)
    DatasetUploadSession.__table__.create(engine)
    Dataset.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    MissionArtifactParent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope(**_context):
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(stage_execution, "get_session", scope)
    return scope


def _mission_with_reconstruction(scope, run_id="a" * 32):
    with scope() as session:
        mission = Mission(
            vol_id=f"mission-{run_id[0]}",
            owner_subject="operator-a",
            workspace_prefix=f"missions/mission-{run_id[0]}",
            status="pending",
            params={"quality_profile": "normal-v1"},
        )
        session.add(mission)
        session.flush()
        reconstruction = MissionStageRun(
            run_id=run_id,
            mission_id=mission.id,
            stage="reconstruction",
            attempt=0,
            status="queued",
            executor="kubernetes-job",
            resource_class="gpu-geometry",
            idempotency_key=run_id[0] * 64,
        )
        training = MissionStageRun(
            mission_id=mission.id,
            stage="gaussian_training",
            attempt=0,
            status="blocked",
            resource_class="gpu-high-memory",
            idempotency_key="b" * 64,
        )
        session.add_all([reconstruction, training])


def test_one_shot_success_publishes_immutable_artifact_and_releases_next_stage(
    execution_sessions,
):
    run_id = "a" * 32
    _mission_with_reconstruction(execution_sessions, run_id)
    observed = {}

    def handler(context, control):
        observed["context"] = context
        assert control.heartbeat()
        return StageExecutionResult(
            kind="reconstruction_workspace",
            uri="s3://drone-ai/missions/mission-a/sparse.tar.zst",
            checksum_sha256="c" * 64,
            size_bytes=1234,
            metadata={"format": "tar.zst"},
            quality_metrics={"registered_images": 80},
            provenance={"colmap_version": "4.0"},
        )

    artifact_id = execute_one_shot_stage(
        "reconstruction",
        handler,
        run_id=run_id,
        heartbeat_interval_seconds=60,
    )

    assert observed["context"].mission_parameters["quality_profile"] == "normal-v1"
    assert observed["context"].organization_id == "legacy-unassigned"
    assert observed["context"].workspace_prefix == "missions/mission-a"
    assert observed["context"].mission_attempt == 0
    with execution_sessions() as session:
        reconstruction = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        training = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "gaussian_training"
        ).one()
        artifact = session.query(MissionArtifact).one()
        assert artifact.artifact_id == artifact_id
        assert artifact.stage_run_id == reconstruction.id
        assert reconstruction.status == "succeeded"
        assert reconstruction.quality_metrics == {"registered_images": 80}
        assert reconstruction.provenance["colmap_version"] == "4.0"
        assert training.status == "queued"
        assert training.upstream_artifact_ids == [artifact_id]
        usage = session.query(OrganizationUsageEvent).one()
        assert usage.action == "storage_reserved"
        assert usage.quantity == 1234


def test_stage_output_over_quota_is_terminal_and_never_enters_graph(
    execution_sessions,
):
    run_id = "e" * 32
    _mission_with_reconstruction(execution_sessions, run_id)
    with execution_sessions() as session:
        session.add(
            Organization(
                id="legacy-unassigned",
                display_name="Legacy",
                status="active",
                created_by="test",
                updated_by="test",
            )
        )
        session.add(
            OrganizationSaasPolicy(
                organization_id="legacy-unassigned",
                storage_limit_bytes=1000,
                version=1,
                created_by="test",
                updated_by="test",
            )
        )

    def handler(_context, _control):
        return StageExecutionResult(
            kind="reconstruction_workspace",
            uri="s3://drone-ai/too-large/manifest.json",
            checksum_sha256="e" * 64,
            size_bytes=1001,
        )

    with pytest.raises(StorageQuotaExceeded):
        execute_one_shot_stage(
            "reconstruction",
            handler,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.status == "failed"
        assert "storage quota exceeded" in run.error_message.lower()
        assert session.query(MissionArtifact).count() == 0
        assert session.query(OrganizationUsageEvent).count() == 0


def test_one_shot_failure_is_terminal_and_keeps_dependants_blocked(
    execution_sessions,
):
    run_id = "d" * 32
    _mission_with_reconstruction(execution_sessions, run_id)

    def handler(_context, _control):
        raise RuntimeError("mapping failed")

    with pytest.raises(RuntimeError, match="mapping failed"):
        execute_one_shot_stage(
            "reconstruction",
            handler,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        runs = {
            run.stage: run for run in session.query(MissionStageRun).all()
        }
        assert runs["reconstruction"].status == "failed"
        assert runs["reconstruction"].error_message == "mapping failed"
        assert runs["gaussian_training"].status == "blocked"
        assert session.query(MissionArtifact).count() == 0


def test_quality_gate_failure_persists_metrics_and_evidence(execution_sessions):
    run_id = "4" * 32
    _mission_with_reconstruction(execution_sessions, run_id)
    metrics = {"gcp_alignment": {"quality_gate": {"accepted": False}}}
    evidence = {
        "persisted": True,
        "uri": "s3://drone-ai/gcp_alignment_report.json",
        "sha256": "a" * 64,
    }

    def handler(_context, _control):
        raise StageQualityGateRejected(
            "GCP quality gate rejected",
            quality_metrics=metrics,
            evidence=evidence,
        )

    with pytest.raises(StageQualityGateRejected, match="GCP quality gate rejected"):
        execute_one_shot_stage(
            "reconstruction",
            handler,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).one()
        assert run.status == "failed"
        assert run.quality_metrics == metrics
        assert run.provenance["quality_gate_rejection"] == evidence
        assert session.query(MissionArtifact).count() == 0


def test_one_shot_loads_exact_inputs_and_persists_parent_edges(execution_sessions):
    run_id = "f" * 32
    source_artifact_id = "12345678-1234-5678-1234-567812345678"
    with execution_sessions() as session:
        mission = Mission(
            vol_id="mission-input",
            owner_subject="operator-a",
            workspace_prefix="missions/mission-input",
            status="pending",
        )
        session.add(mission)
        session.flush()
        source_run = MissionStageRun(
            mission_id=mission.id,
            stage="reconstruction",
            attempt=0,
            status="succeeded",
            idempotency_key="1" * 64,
        )
        session.add(source_run)
        session.flush()
        session.add(
            MissionArtifact(
                artifact_id=source_artifact_id,
                mission_id=mission.id,
                stage_run_id=source_run.id,
                kind="sparse_reconstruction",
                uri="s3://drone-ai/sparse.tar.zst",
                checksum_sha256="1" * 64,
            )
        )
        session.add(
            MissionStageRun(
                run_id=run_id,
                mission_id=mission.id,
                stage="gaussian_training",
                attempt=0,
                status="queued",
                executor="kubernetes-job",
                resource_class="gpu-high-memory",
                upstream_artifact_ids=[source_artifact_id],
                idempotency_key="2" * 64,
            )
        )

    def handler(context, _control):
        assert [item.artifact_id for item in context.inputs] == [source_artifact_id]
        return StageExecutionResult(
            kind="gaussian_training_workspace",
            uri="s3://drone-ai/checkpoint.ply",
            checksum_sha256="2" * 64,
            size_bytes=128,
        )

    execute_one_shot_stage(
        "gaussian_training",
        handler,
        run_id=run_id,
        heartbeat_interval_seconds=60,
    )

    with execution_sessions() as session:
        edge = session.query(MissionArtifactParent).one()
        assert edge.parent.artifact_id == source_artifact_id
        assert session.query(MissionArtifact).filter(
            MissionArtifact.id == edge.artifact_id
        ).one().kind == "gaussian_training_workspace"


def test_one_shot_rejects_wrong_artifact_kind(execution_sessions):
    run_id = "9" * 32
    _mission_with_reconstruction(execution_sessions, run_id)

    with pytest.raises(ValueError, match="must publish artifact kind"):
        execute_one_shot_stage(
            "reconstruction",
            lambda _context, _control: StageExecutionResult(
                kind="detection_workspace",
                uri="s3://drone-ai/wrong.json",
                checksum_sha256="9" * 64,
                size_bytes=1,
            ),
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).one()
        assert run.status == "failed"
        assert session.query(MissionArtifact).count() == 0


def test_one_shot_observes_durable_mission_cancellation(execution_sessions):
    run_id = "e" * 32
    _mission_with_reconstruction(execution_sessions, run_id)

    def handler(_context, control):
        with execution_sessions() as session:
            session.query(Mission).one().status = "cancelled"
        control.raise_if_cancelled()
        raise AssertionError("cancelled execution must stop")

    with pytest.raises(StageExecutionCancelled):
        execute_one_shot_stage(
            "reconstruction",
            handler,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        assert run.status == "cancelled"
        assert run.error_message is None
        assert session.query(MissionArtifact).count() == 0


def test_one_shot_normalizes_domain_cancellation_errors(execution_sessions):
    run_id = "c" * 32
    _mission_with_reconstruction(execution_sessions, run_id)

    def handler(_context, _control):
        with execution_sessions() as session:
            session.query(Mission).one().status = "cancelled"
        raise RuntimeError("Mission cancelled by user")

    with pytest.raises(StageExecutionCancelled) as captured:
        execute_one_shot_stage(
            "reconstruction",
            handler,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    assert isinstance(captured.value.__cause__, RuntimeError)
    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.stage == "reconstruction"
        ).one()
        assert run.status == "cancelled"
        assert run.current_step == "CANCELLED"
        assert run.error_message is None
        assert session.query(MissionArtifact).count() == 0


def test_stage_subtask_keeps_run_active_without_artifact_authority(
    execution_sessions,
):
    run_id = "7" * 32
    _mission_with_reconstruction(execution_sessions, run_id)
    observed = {}

    def handler(context, control):
        observed["run_id"] = context.run_id
        assert control.heartbeat()

    execute_stage_subtask(
        "reconstruction",
        handler,
        run_id=run_id,
        heartbeat_interval_seconds=60,
    )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).one()
        assert observed["run_id"] == run_id
        assert run.status == "running"
        assert run.completed_at is None
        assert session.query(MissionArtifact).count() == 0


def test_stage_subtask_failure_marks_the_parent_run_failed(execution_sessions):
    run_id = "8" * 32
    _mission_with_reconstruction(execution_sessions, run_id)

    def fail(_context, _control):
        raise RuntimeError("shard failed")

    with pytest.raises(RuntimeError, match="shard failed"):
        execute_stage_subtask(
            "reconstruction",
            fail,
            run_id=run_id,
            heartbeat_interval_seconds=60,
        )

    with execution_sessions() as session:
        run = session.query(MissionStageRun).filter(
            MissionStageRun.run_id == run_id
        ).one()
        assert run.status == "failed"
        assert run.error_message == "shard failed"
        assert session.query(MissionArtifact).count() == 0


def test_result_contract_rejects_non_sha256_content():
    with pytest.raises(ValueError, match="SHA-256"):
        StageExecutionResult(
            kind="orthomosaic",
            uri="s3://bucket/ortho.tif",
            checksum_sha256="NOT-A-CHECKSUM",
            size_bytes=1,
        )
