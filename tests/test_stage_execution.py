from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
)
from shared import stage_execution
from shared.stage_execution import (
    StageExecutionCancelled,
    StageExecutionResult,
    execute_one_shot_stage,
)


@pytest.fixture
def execution_sessions(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    MissionArtifactParent.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def scope():
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
            kind="sparse_reconstruction",
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


def test_one_shot_loads_exact_inputs_and_persists_parent_edges(execution_sessions):
    run_id = "f" * 32
    source_artifact_id = "12345678-1234-5678-1234-567812345678"
    with execution_sessions() as session:
        mission = Mission(
            vol_id="mission-input",
            owner_subject="operator-a",
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
            kind="gaussian_checkpoint",
            uri="s3://drone-ai/checkpoint.ply",
            checksum_sha256="2" * 64,
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
        ).one().kind == "gaussian_checkpoint"


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


def test_result_contract_rejects_non_sha256_content():
    with pytest.raises(ValueError, match="SHA-256"):
        StageExecutionResult(
            kind="orthomosaic",
            uri="s3://bucket/ortho.tif",
            checksum_sha256="NOT-A-CHECKSUM",
        )
