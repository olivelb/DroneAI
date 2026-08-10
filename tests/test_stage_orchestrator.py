import importlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.artifact_manifest import content_addressed_blob_key
from shared.database import (
    DetectionShardReceipt,
    Mission,
    MissionArtifact,
    MissionStageRun,
)
from shared.detection_sharding import parse_detection_shard_plan_descriptor
from shared.stage_scheduler import SchedulingLimits

orchestrator = importlib.import_module("app4-dashboard.api.stage_orchestrator")


def _executors():
    image = "registry.example/worker@sha256:" + "a" * 64
    return {
        stage: orchestrator.StageExecutorConfig(
            image=image,
            command=("python", "-m", f"{stage}_executor"),
            gpu_architecture="ampere",
            tolerations=(
                orchestrator.StageJobToleration(
                    key="nvidia.com/gpu",
                    value="present",
                    effect="NoSchedule",
                ),
            ),
        )
        for stage in (
            "reconstruction",
            "gaussian_training",
            "gaussian_filtering",
            "rasterization",
            "detection",
        )
    }


def _settings(**kwargs):
    values = {
        "enabled": True,
        "namespace": "drone-ai",
        "poll_seconds": 1.0,
        "limits": SchedulingLimits(global_active=2, per_owner_active=1),
        "executors": _executors(),
        "runtime_class_name": "nvidia",
    }
    values.update(kwargs)
    return orchestrator.StageOrchestratorSettings(**values)


@pytest.fixture
def stage_sessions():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    DetectionShardReceipt.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
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

    return scope


def _add_run(
    scope,
    vol_id,
    owner,
    run_id,
    *,
    status="queued",
    stage="reconstruction",
    resource_class="gpu-geometry",
):
    with scope() as session:
        mission = Mission(vol_id=vol_id, owner_subject=owner, status="pending")
        session.add(mission)
        session.flush()
        session.add(
            MissionStageRun(
                run_id=run_id,
                mission_id=mission.id,
                stage=stage,
                attempt=0,
                status=status,
                idempotency_key=run_id[0] * 64,
                resource_class=resource_class,
            )
        )


def _add_detection_run(scope, run_id, *, width, height, tile_size=1024):
    artifact_id = f"raster-{run_id}"
    with scope() as session:
        mission = Mission(
            vol_id=f"mission-{run_id}",
            owner_subject="owner-detection",
            status="pending",
        )
        session.add(mission)
        session.flush()
        raster_run = MissionStageRun(
            run_id=f"raster-{run_id}",
            mission_id=mission.id,
            stage="rasterization",
            attempt=0,
            status="succeeded",
            idempotency_key="1" * 64,
            resource_class="gpu-standard",
            quality_metrics={"width": width, "height": height},
        )
        session.add(raster_run)
        session.flush()
        session.add(
            MissionArtifact(
                artifact_id=artifact_id,
                mission_id=mission.id,
                stage_run_id=raster_run.id,
                kind="raster_product_workspace",
                uri="s3://drone-ai/raster/manifest.json",
                checksum_sha256="2" * 64,
                artifact_metadata={"ortho_file": "orthomosaic.tif"},
            )
        )
        session.add(
            MissionStageRun(
                run_id=run_id,
                mission_id=mission.id,
                stage="detection",
                attempt=0,
                status="queued",
                idempotency_key="3" * 64,
                resource_class="gpu-standard",
                parameters={"ai": {"tile_size": tile_size}},
                upstream_artifact_ids=[artifact_id],
            )
        )


def test_reservation_is_fair_persistent_and_records_executor_provenance(stage_sessions):
    _add_run(stage_sessions, "mission-a", "owner-a", "a" * 32)
    _add_run(stage_sessions, "mission-b", "owner-b", "b" * 32)
    _add_run(stage_sessions, "mission-c", "owner-a", "c" * 32)

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )

    assert {item.request.owner_subject for item in reserved} == {"owner-a", "owner-b"}
    assert all(item.config.runtime_class_name == "nvidia" for item in reserved)
    assert all(
        item.config.tolerations == _executors()[item.request.stage].tolerations
        for item in reserved
    )
    with stage_sessions() as session:
        scheduled = session.query(MissionStageRun).filter(
            MissionStageRun.executor == "kubernetes-job"
        ).all()
        assert len(scheduled) == 2
        assert all(run.dispatch_attempts == 1 for run in scheduled)
        assert all(run.job_name.startswith("droneai-") for run in scheduled)
        assert all(run.provenance["gpu_architecture"] == "ampere" for run in scheduled)


def test_reservation_repairs_legacy_cpu_rasterization_before_dispatch(stage_sessions):
    run_id = "9" * 32
    _add_run(
        stage_sessions,
        "mission-raster-repair",
        "owner-a",
        run_id,
        stage="rasterization",
        resource_class="cpu-standard",
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )

    assert reserved[0].request.resource_class == "gpu-standard"
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.resource_class == "gpu-standard"
        assert run.provenance["resource_class_repaired_from"] == "cpu-standard"


def test_reservation_skips_when_another_postgres_scheduler_owns_the_lock():
    statements = []

    class LockResult:
        @staticmethod
        def scalar_one():
            return False

    class LockedSession:
        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def execute(statement, parameters):
            statements.append((str(statement), parameters))
            return LockResult()

        @staticmethod
        def query(*_args):
            raise AssertionError("Reservation queries must not run without the lock")

    reserved = orchestrator.reserve_ready_jobs(
        LockedSession(),
        _settings(),
        datetime.now(UTC),
    )

    assert reserved == []
    assert "pg_try_advisory_xact_lock" in statements[0][0]
    assert statements[0][1] == {
        "namespace": orchestrator.SCHEDULER_LOCK_NAMESPACE,
        "lock_key": orchestrator.SCHEDULER_LOCK_KEY,
    }


class FakeJobClient:
    def __init__(self, jobs=None):
        self.jobs = jobs or {}
        self.created = []
        self.deleted = []

    def create(self, job):
        self.created.append(job)
        self.jobs[job["metadata"]["name"]] = job
        return job

    def get(self, name):
        if name not in self.jobs:
            raise orchestrator.KubernetesApiError(404, "missing")
        return self.jobs[name]

    def delete(self, name):
        self.deleted.append(name)
        self.jobs.pop(name, None)
        return {}


def test_reconciliation_tracks_heartbeat_and_fails_artifactless_success(
    stage_sessions,
    monkeypatch,
):
    run_id = "d" * 32
    _add_run(stage_sessions, "mission-running", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(session, _settings(), datetime.now(UTC))
    name = reserved[0].job_name
    client = FakeJobClient({name: {"status": {"active": 1}}})
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, _settings())
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.status == "running"
        assert run.heartbeat_at is not None

    client.jobs[name] = {"status": {"succeeded": 1}}
    orchestrator.reconcile_stage_jobs(client, _settings())
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.status == "failed"
        assert "immutable artifact" in run.error_message


def test_reconciliation_deletes_jobs_for_cancelled_missions(stage_sessions, monkeypatch):
    run_id = "e" * 32
    _add_run(stage_sessions, "mission-cancelled", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(session, _settings(), datetime.now(UTC))
        mission = session.query(Mission).one()
        mission.status = "cancelled"
    client = FakeJobClient({reserved[0].job_name: {"status": {"active": 1}}})
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, _settings())

    assert client.deleted == [reserved[0].job_name]
    with stage_sessions() as session:
        assert session.query(MissionStageRun).one().status == "cancelled"


def test_reconciliation_idempotently_recreates_a_disappeared_job(
    stage_sessions,
    monkeypatch,
):
    run_id = "f" * 32
    _add_run(stage_sessions, "mission-recreate", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(session, _settings(), datetime.now(UTC))
    client = FakeJobClient()
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, _settings())

    assert len(client.created) == 1
    assert client.created[0]["metadata"]["name"] == reserved[0].job_name
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.dispatch_attempts == 2
        assert run.dispatch_error is None


def test_enabled_settings_require_complete_immutable_one_shot_catalog(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_STAGE_EXECUTORS_JSON", "{}")

    with pytest.raises(ValueError, match="Missing one-shot executor"):
        orchestrator.settings_from_environment()


def test_executor_catalog_parses_stage_tolerations():
    payload = {
        stage: {
            "image": "registry.example/worker@sha256:" + "a" * 64,
            "command": ["python", "-m", f"{stage}_executor"],
            "gpu_architecture": "ampere",
            "tolerations": [
                {
                    "key": "nvidia.com/gpu",
                    "operator": "Equal",
                    "value": "present",
                    "effect": "NoSchedule",
                }
            ],
        }
        for stage in _executors()
    }

    catalog = orchestrator._executor_catalog(json.dumps(payload))

    assert catalog["detection"].tolerations == (
        orchestrator.StageJobToleration(
            key="nvidia.com/gpu",
            operator="Equal",
            value="present",
            effect="NoSchedule",
        ),
    )


def test_protected_stage_jobs_require_distinct_scoped_credential_secrets(
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_ENV", "staging")

    with pytest.raises(ValueError, match="one distinct credential Secret"):
        orchestrator.settings_from_environment()


def test_stage_credential_secret_map_is_complete_and_never_shared(monkeypatch):
    incomplete = {stage: f"credentials-{stage}" for stage in _executors()}
    incomplete.pop("detection")
    monkeypatch.setenv(
        "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON",
        json.dumps(incomplete),
    )
    with pytest.raises(ValueError, match="Missing stage credential Secret entries"):
        orchestrator.settings_from_environment()

    shared = {stage: "shared-credentials" for stage in _executors()}
    monkeypatch.setenv(
        "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON",
        json.dumps(shared),
    )
    with pytest.raises(ValueError, match="distinct credential Secret"):
        orchestrator.settings_from_environment()


@pytest.mark.parametrize("stage", tuple(_executors()))
def test_reserved_jobs_receive_only_their_stage_credential_secret(
    stage_sessions,
    monkeypatch,
    stage,
):
    secret_names = {stage: f"credentials-{stage}" for stage in _executors()}
    monkeypatch.setenv(
        "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON",
        json.dumps(secret_names),
    )
    parsed = orchestrator.settings_from_environment()
    _add_run(
        stage_sessions,
        "mission-scoped",
        "owner-a",
        "6" * 32,
        stage=stage,
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(
                job_secret_environment_by_stage=(
                    parsed.job_secret_environment_by_stage
                ),
            ),
            datetime.now(UTC),
        )

    secrets = reserved[0].config.secret_environment
    assert {item.secret_name for item in secrets} == {f"credentials-{stage}"}
    assert {item.name for item in secrets} == {
        "DATABASE_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
    }


def test_settings_forward_explicit_v2_writer_rollout_to_stage_jobs(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")
    monkeypatch.setenv("DRONEAI_ARTIFACT_MANIFEST_V2_WRITE_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED", "true")

    settings = orchestrator.settings_from_environment()

    assert (
        "DRONEAI_ARTIFACT_MANIFEST_V2_WRITE_ENABLED",
        "true",
    ) in settings.job_environment
    assert (
        "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED",
        "true",
    ) in settings.job_environment


def test_detection_fanout_settings_fail_closed_without_manifest_v2(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")
    monkeypatch.setenv("DRONEAI_DETECTION_FANOUT_ENABLED", "true")

    with pytest.raises(ValueError, match="Manifest v2 writes and selective restore"):
        orchestrator.settings_from_environment()

    monkeypatch.setenv("DRONEAI_ARTIFACT_MANIFEST_V2_WRITE_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_DETECTION_TILES_PER_SHARD", "64")
    monkeypatch.setenv("DRONEAI_DETECTION_SHARD_PARALLELISM", "3")
    settings = orchestrator.settings_from_environment()

    assert settings.detection_fanout_enabled is True
    assert settings.detection_tiles_per_shard == 64
    assert settings.detection_shard_parallelism == 3


def test_large_detection_reservation_persists_plan_and_builds_indexed_job(
    stage_sessions,
):
    run_id = "7" * 32
    _add_detection_run(stage_sessions, run_id, width=55_000, height=55_000)
    settings = _settings(
        detection_fanout_enabled=True,
        detection_tiles_per_shard=1_024,
        detection_shard_parallelism=2,
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        )

    assert len(reserved) == 1
    assert reserved[0].config.indexed is not None
    assert reserved[0].config.indexed.completions == 5
    assert reserved[0].config.indexed.parallelism == 2
    assert ("DRONEAI_DETECTION_EXECUTION_MODE", "shard") in (
        reserved[0].config.environment
    )
    manifest = orchestrator.build_stage_job(
        reserved[0].request,
        reserved[0].config,
    )
    assert manifest["spec"]["completionMode"] == "Indexed"
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        plan = parse_detection_shard_plan_descriptor(
            run.provenance["detection_shard_plan"]
        )
        assert plan.shard_count == 5
        assert run.provenance[orchestrator.DETECTION_PHASE_KEY] == "shards"


def test_small_detection_reservation_remains_monolithic(stage_sessions):
    run_id = "8" * 32
    _add_detection_run(stage_sessions, run_id, width=2_000, height=2_000)

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(detection_fanout_enabled=True),
            datetime.now(UTC),
        )

    assert len(reserved) == 1
    assert reserved[0].config.indexed is None
    assert not any(
        name == "DRONEAI_DETECTION_EXECUTION_MODE"
        for name, _value in reserved[0].config.environment
    )
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.provenance["detection_execution_mode"] == "monolithic"
        assert "detection_shard_plan" not in run.provenance


def test_completed_detection_shards_dispatch_a_distinct_finalizer(
    stage_sessions,
    monkeypatch,
):
    run_id = "6" * 32
    _add_detection_run(stage_sessions, run_id, width=55_000, height=55_000)
    settings = _settings(detection_fanout_enabled=True)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        )
    shard_job_name = reserved[0].job_name
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        run.status = "running"
        plan = parse_detection_shard_plan_descriptor(
            run.provenance["detection_shard_plan"]
        )
        for shard in plan.shards:
            checksum = f"{shard.shard_index + 1:064x}"
            session.add(
                DetectionShardReceipt(
                    stage_run_id=run.id,
                    plan_checksum_sha256=plan.checksum_sha256,
                    shard_index=shard.shard_index,
                    shard_count=plan.shard_count,
                    tile_count=shard.tile_count,
                    result_key=content_addressed_blob_key(checksum),
                    result_checksum_sha256=checksum,
                    result_size_bytes=100 + shard.shard_index,
                )
            )
    client = FakeJobClient(
        {shard_job_name: {"status": {"succeeded": plan.shard_count}}}
    )
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, settings)

    finalizer_name = orchestrator.stage_job_name(f"{run_id}-finalizer")
    assert client.created[0]["metadata"]["name"] == finalizer_name
    assert "completionMode" not in client.created[0]["spec"]
    environment = {
        item["name"]: item.get("value")
        for item in client.created[0]["spec"]["template"]["spec"]["containers"][0][
            "env"
        ]
    }
    assert environment["DRONEAI_DETECTION_EXECUTION_MODE"] == "finalizer"
    assert environment["DRONEAI_STAGE_RUN_ID"] == run_id
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.status == "running"
        assert run.job_name == finalizer_name
        assert run.current_step == "DETECTION_FINALIZING"
        assert run.provenance[orchestrator.DETECTION_PHASE_KEY] == "finalizer"
        assert run.provenance["detection_shard_job_name"] == shard_job_name


def test_completed_indexed_job_fails_closed_when_a_receipt_is_missing(
    stage_sessions,
    monkeypatch,
):
    run_id = "5" * 32
    _add_detection_run(stage_sessions, run_id, width=55_000, height=55_000)
    settings = _settings(detection_fanout_enabled=True)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        )
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        run.status = "running"
    client = FakeJobClient(
        {reserved[0].job_name: {"status": {"succeeded": 5}}}
    )
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, settings)

    assert client.created == []
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.status == "failed"
        assert "complete durable receipt set" in run.error_message
        assert "missing=" in run.error_message


def test_detection_job_alone_receives_model_configuration_and_hf_token():
    model_environment = (
        ("SAM3_MODEL_ID", "facebook/sam3"),
        ("SAM3_MODEL_REVISION", "3" * 40),
    )
    model_secret = (
        orchestrator.SecretEnvironment("HF_TOKEN", "hf-token", "HF_TOKEN"),
    )
    settings = _settings(
        detection_environment=model_environment,
        detection_secret_environment=model_secret,
    )
    mission = Mission(
        id=42,
        vol_id="mission-model-scope",
        owner_subject="operator@example.test",
    )

    detection = orchestrator._reserved_job(
        SimpleNamespace(
            run_id="d" * 32,
            stage="detection",
            resource_class="gpu-high-memory",
        ),
        mission,
        settings,
    )
    reconstruction = orchestrator._reserved_job(
        SimpleNamespace(
            run_id="r" * 32,
            stage="reconstruction",
            resource_class="gpu-geometry",
        ),
        mission,
        settings,
    )

    assert detection.config.environment == model_environment
    assert detection.config.secret_environment == model_secret
    assert reconstruction.config.environment == ()
    assert reconstruction.config.secret_environment == ()
