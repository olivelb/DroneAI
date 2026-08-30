
import importlib
import hashlib
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
    Organization,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.detection_sharding import parse_detection_shard_plan_descriptor
from shared.stage_scheduler import SchedulingLimits
from shared.tenancy import mission_prefix

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
            "gaussian_viewer",
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
    Organization.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionStageRun.__table__.create(engine)
    DetectionShardReceipt.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    OrganizationSaasPolicy.__table__.create(engine)
    OrganizationUsageEvent.__table__.create(engine)
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
    mission_params=None,
    run_parameters=None,
    organization_id="legacy-unassigned",
):
    with scope() as session:
        if session.get(Organization, organization_id) is None:
            session.add(
                Organization(
                    id=organization_id,
                    display_name=organization_id,
                    status="active",
                    created_by="test",
                    updated_by="test",
                )
            )
        mission = Mission(
            vol_id=vol_id,
            owner_subject=owner,
            organization_id=organization_id,
            workspace_prefix=mission_prefix(organization_id, vol_id),
            status="pending",
            params=mission_params,
        )
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
                parameters=run_parameters,
            )
        )


def _add_detection_run(
    scope,
    run_id,
    *,
    width,
    height,
    tile_size=1024,
    owner="owner-detection",
    organization_id="legacy-unassigned",
):
    artifact_id = f"raster-{run_id}"
    with scope() as session:
        if session.get(Organization, organization_id) is None:
            session.add(
                Organization(
                    id=organization_id,
                    display_name=organization_id,
                    status="active",
                    created_by="test",
                    updated_by="test",
                )
            )
        mission = Mission(
            vol_id=f"mission-{run_id}",
            owner_subject=owner,
            organization_id=organization_id,
            workspace_prefix=mission_prefix(
                organization_id,
                f"mission-{run_id}",
            ),
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
            idempotency_key=hashlib.sha256(
                f"rasterization:{run_id}".encode()
            ).hexdigest(),
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
                idempotency_key=hashlib.sha256(
                    f"detection:{run_id}".encode()
                ).hexdigest(),
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
    assert all(
        dict(item.config.node_selector)[orchestrator.GPU_ARCHITECTURE_LABEL]
        == "ampere"
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


def test_reservation_enforces_organization_policy_and_audits_usage(stage_sessions):
    _add_run(
        stage_sessions,
        "mission-a1",
        "member-a",
        "1" * 32,
        organization_id="tenant-a",
    )
    _add_run(
        stage_sessions,
        "mission-a2",
        "member-a",
        "2" * 32,
        organization_id="tenant-a",
    )
    _add_run(
        stage_sessions,
        "mission-b1",
        "member-b",
        "3" * 32,
        organization_id="tenant-b",
    )
    with stage_sessions() as session:
        session.add(
            OrganizationSaasPolicy(
                organization_id="tenant-a",
                concurrent_stage_runs_limit=1,
                version=1,
                created_by="platform-support",
                updated_by="platform-support",
            )
        )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(
                limits=SchedulingLimits(
                    global_active=3,
                    per_owner_active=3,
                )
            ),
            datetime.now(UTC),
        )

    assert {item.request.organization_id for item in reserved} == {
        "tenant-a",
        "tenant-b",
    }
    with stage_sessions() as session:
        assert session.query(OrganizationUsageEvent).filter_by(
            action="stage_scheduled"
        ).count() == 2


def test_suspended_organization_runs_are_not_dispatched(stage_sessions):
    _add_run(
        stage_sessions,
        "mission-suspended",
        "member-a",
        "6" * 32,
        organization_id="tenant-suspended",
    )
    _add_run(
        stage_sessions,
        "mission-active",
        "member-b",
        "7" * 32,
        organization_id="tenant-active",
    )
    with stage_sessions() as session:
        session.get(Organization, "tenant-suspended").status = "suspended"

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )

    assert [item.request.organization_id for item in reserved] == [
        "tenant-active"
    ]


def test_scheduler_scans_beyond_first_candidate_page(stage_sessions):
    with stage_sessions() as session:
        for organization_id in ("tenant-a", "tenant-b"):
            session.add(
                Organization(
                    id=organization_id,
                    display_name=organization_id,
                    status="active",
                    created_by="test",
                    updated_by="test",
                )
            )
        mission_a = Mission(
            vol_id="backlogged",
            owner_subject="member-a",
            organization_id="tenant-a",
            workspace_prefix=mission_prefix("tenant-a", "backlogged"),
            status="pending",
        )
        mission_b = Mission(
            vol_id="runnable",
            owner_subject="member-b",
            organization_id="tenant-b",
            workspace_prefix=mission_prefix("tenant-b", "runnable"),
            status="pending",
        )
        session.add_all((mission_a, mission_b))
        session.flush()
        session.add(
            MissionStageRun(
                run_id="f" * 32,
                mission_id=mission_a.id,
                stage="reconstruction",
                attempt=1_000,
                status="running",
                idempotency_key="f" * 64,
                executor="kubernetes-job",
                job_name="active-owner-a",
                resource_class="gpu-geometry",
            )
        )
        for attempt in range(500):
            run_id = f"{attempt:032x}"
            session.add(
                MissionStageRun(
                    run_id=run_id,
                    mission_id=mission_a.id,
                    stage="reconstruction",
                    attempt=attempt,
                    status="queued",
                    idempotency_key=hashlib.sha256(run_id.encode()).hexdigest(),
                    resource_class="gpu-geometry",
                )
            )
        session.add(
            MissionStageRun(
                run_id="e" * 32,
                mission_id=mission_b.id,
                stage="reconstruction",
                attempt=0,
                status="queued",
                idempotency_key="e" * 64,
                resource_class="gpu-geometry",
            )
        )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )

    assert [item.request.organization_id for item in reserved] == ["tenant-b"]


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


def test_reservation_routes_work_to_the_mission_selected_drive(stage_sessions):
    run_id = "4" * 32
    _add_run(
        stage_sessions,
        "mission-work-drive",
        "owner-a",
        run_id,
        stage="rasterization",
        resource_class="gpu-high-memory",
        mission_params={"work_drive": "drive-j"},
        run_parameters={"work_drive": "drive-j"},
    )
    work_volume = orchestrator.StageJobWorkVolume(
        {"hostPath": {"path": "/mnt/j/.droneai/work", "type": "Directory"}}
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(
                work_drives={"drive-j": work_volume},
                work_drive_default="drive-j",
            ),
            datetime.now(UTC),
        )

    assert reserved[0].config.work_volume == work_volume
    manifest = orchestrator.build_stage_job(
        reserved[0].request,
        reserved[0].config,
    )
    work = next(
        volume
        for volume in manifest["spec"]["template"]["spec"]["volumes"]
        if volume["name"] == "work"
    )
    assert work["hostPath"]["path"] == "/mnt/j/.droneai/work"


def test_reservation_fails_the_run_when_selected_work_drive_disappears(
    stage_sessions,
):
    run_id = "2" * 32
    _add_run(
        stage_sessions,
        "mission-missing-drive",
        "owner-a",
        run_id,
        mission_params={"work_drive": "drive-j"},
        run_parameters={"work_drive": "drive-j"},
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(
                work_drives={
                    "system": orchestrator.StageJobWorkVolume(
                        {"emptyDir": {"sizeLimit": "100Gi"}}
                    )
                },
                work_drive_default="system",
            ),
            datetime.now(UTC),
        )

    assert reserved == []
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.status == "failed"
        assert "work_drive is not configured" in run.error_message


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


class ConflictJobClient(FakeJobClient):
    def __init__(self, existing):
        super().__init__()
        self.existing = existing

    def create(self, job):
        raise orchestrator.KubernetesApiError(409, "already exists")

    def get(self, name):
        return self.existing


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


def test_reconciliation_persists_kubernetes_deadline_reason(
    stage_sessions,
    monkeypatch,
):
    run_id = "6" * 32
    _add_run(stage_sessions, "mission-deadline", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )
    client = FakeJobClient(
        {
            reserved[0].job_name: {
                "status": {
                    "failed": 1,
                    "conditions": [
                        {
                            "type": "FailureTarget",
                            "status": "True",
                            "reason": "DeadlineExceeded",
                            "message": "Job exceeded its active deadline",
                        }
                    ],
                }
            }
        }
    )
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, _settings())

    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.status == "failed"
        assert run.error_message == (
            "Kubernetes stage Job failed: DeadlineExceeded: "
            "Job exceeded its active deadline"
        )


def test_reconciliation_deletes_jobs_for_cancelled_missions(stage_sessions, monkeypatch):
    run_id = "e" * 32
    _add_run(stage_sessions, "mission-cancelled", "owner-a", run_id)
    completed_at = datetime(2026, 8, 10, 12, 30)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(session, _settings(), datetime.now(UTC))
        mission = session.query(Mission).one()
        mission.status = "cancelled"
        run = session.query(MissionStageRun).one()
        run.status = "cancelled"
        run.completed_at = completed_at
    client = FakeJobClient({reserved[0].job_name: {"status": {"active": 1}}})
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, _settings())
    orchestrator.reconcile_stage_jobs(client, _settings())

    assert client.deleted == [reserved[0].job_name]
    with stage_sessions() as session:
        run = session.query(MissionStageRun).one()
        assert run.status == "cancelled"
        assert run.completed_at == completed_at
        assert run.provenance[orchestrator.CANCELLATION_JOB_CLEANUP_AT_KEY]


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


def test_reconciliation_recreates_a_legacy_detection_finalizer_on_cpu(
    stage_sessions,
    monkeypatch,
):
    run_id = "0" * 32
    _add_detection_run(stage_sessions, run_id, width=55_000, height=55_000)
    settings = _settings(detection_fanout_enabled=True)
    with stage_sessions() as session:
        orchestrator.reserve_ready_jobs(session, settings, datetime.now(UTC))
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        run.provenance = {
            **run.provenance,
            orchestrator.DETECTION_PHASE_KEY: "finalizer",
        }
        run.job_name = orchestrator.stage_job_name(f"{run_id}-finalizer")
        run.resource_class = "gpu-standard"

    client = FakeJobClient()
    monkeypatch.setattr(orchestrator, "get_session", stage_sessions)

    orchestrator.reconcile_stage_jobs(client, settings)

    assert len(client.created) == 1
    pod = client.created[0]["spec"]["template"]["spec"]
    assert "nodeSelector" not in pod
    assert "tolerations" not in pod
    assert "runtimeClassName" not in pod
    assert "nvidia.com/gpu" not in (
        pod["containers"][0]["resources"]["limits"]
    )
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.resource_class == "cpu-standard"
        assert run.provenance[
            orchestrator.DETECTION_INFERENCE_RESOURCE_CLASS_KEY
        ] == "gpu-standard"


def test_enabled_settings_require_complete_immutable_one_shot_catalog(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_STAGE_EXECUTORS_JSON", "{}")

    with pytest.raises(ValueError, match="Missing one-shot executor"):
        orchestrator.settings_from_environment()


def test_protected_settings_reject_fused_compute(monkeypatch):
    monkeypatch.setenv("DRONEAI_ENV", "staging")
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")

    with pytest.raises(RuntimeError, match="require bounded stage Jobs"):
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


def test_protected_executor_catalog_rejects_mutable_git_sha_tags():
    payload = {
        stage: {
            "image": "registry.example/worker:0123456789abcdef",
            "command": ["python", "-m", f"{stage}_executor"],
            "gpu_architecture": "ampere",
        }
        for stage in _executors()
    }

    with pytest.raises(ValueError, match="must use an OCI digest"):
        orchestrator._executor_catalog(json.dumps(payload), require_digest=True)

    assert orchestrator._executor_catalog(json.dumps(payload))["detection"].image == (
        "registry.example/worker:0123456789abcdef"
    )


def test_work_drive_catalog_supports_local_pvc_and_bounded_empty_dir():
    catalog = orchestrator._work_drive_catalog(
        json.dumps(
            [
                {"name": "drive-j", "hostPath": "/mnt/j/.droneai/work"},
                {"name": "cloud", "existingClaim": "droneai-work"},
                {"name": "system", "type": "emptyDir"},
            ]
        ),
        empty_dir_size_limit="120Gi",
    )

    assert catalog["drive-j"].source["hostPath"]["type"] == "Directory"
    assert catalog["cloud"].source["persistentVolumeClaim"] == {
        "claimName": "droneai-work"
    }
    assert catalog["system"].source["emptyDir"] == {"sizeLimit": "120Gi"}


def test_work_drive_catalog_fails_closed_on_ambiguous_sources():
    with pytest.raises(ValueError, match="one supported source"):
        orchestrator._work_drive_catalog(
            json.dumps(
                [
                    {
                        "name": "ambiguous",
                        "hostPath": "/mnt/j/work",
                        "existingClaim": "droneai-work",
                    }
                ]
            ),
            empty_dir_size_limit="100Gi",
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
    assert next(
        item for item in secrets if item.name == "DATABASE_URL"
    ).secret_key == "stage-database-url"


def test_protected_stage_jobs_require_rls_at_the_executor_boundary(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "true")
    monkeypatch.setenv("DRONEAI_ENV", "production")
    monkeypatch.setenv(
        "DRONEAI_STAGE_CREDENTIAL_SECRETS_JSON",
        json.dumps({stage: f"credentials-{stage}" for stage in _executors()}),
    )
    monkeypatch.setenv(
        "DRONEAI_STAGE_EXECUTORS_JSON",
        json.dumps(
            {
                stage: {
                    "image": executor.image,
                    "command": list(executor.command),
                    "gpu_architecture": executor.gpu_architecture,
                }
                for stage, executor in _executors().items()
            }
        ),
    )

    settings = orchestrator.settings_from_environment()

    assert ("DRONEAI_STAGE_RLS_REQUIRED", "true") in settings.job_environment


def test_settings_forward_selective_restore_to_stage_jobs(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")
    monkeypatch.setenv("DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED", "true")

    settings = orchestrator.settings_from_environment()

    assert (
        "DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED",
        "true",
    ) in settings.job_environment


def test_detection_fanout_settings_fail_closed_without_selective_restore(monkeypatch):
    monkeypatch.setenv("DRONEAI_STAGE_JOBS_ENABLED", "false")
    monkeypatch.setenv("DRONEAI_DETECTION_FANOUT_ENABLED", "true")

    with pytest.raises(ValueError, match="selective restore"):
        orchestrator.settings_from_environment()

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
        assert run.provenance[
            orchestrator.DETECTION_REQUESTED_PARALLELISM_KEY
        ] == 2
        assert run.provenance[
            orchestrator.DETECTION_EFFECTIVE_PARALLELISM_KEY
        ] == 2


def test_detection_fanout_never_reserves_more_physical_gpu_units_than_capacity(
    stage_sessions,
):
    first_run_id = "a" * 32
    second_run_id = "b" * 32
    _add_detection_run(
        stage_sessions,
        first_run_id,
        width=55_000,
        height=55_000,
        owner="member-a",
        organization_id="tenant-a",
    )
    _add_detection_run(
        stage_sessions,
        second_run_id,
        width=55_000,
        height=55_000,
        owner="member-b",
        organization_id="tenant-b",
    )
    settings = _settings(
        limits=SchedulingLimits(
            global_active=2,
            resource_active={"gpu-standard": 2},
        ),
        detection_fanout_enabled=True,
        detection_tiles_per_shard=1_024,
        detection_shard_parallelism=4,
    )

    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        )

    assert len(reserved) == 1
    assert reserved[0].request.organization_id == "tenant-a"
    assert reserved[0].config.indexed is not None
    assert reserved[0].config.indexed.parallelism == 2
    with stage_sessions() as session:
        first = session.query(MissionStageRun).filter_by(
            run_id=first_run_id
        ).one()
        second = session.query(MissionStageRun).filter_by(
            run_id=second_run_id
        ).one()
        assert first.provenance[
            orchestrator.DETECTION_REQUESTED_PARALLELISM_KEY
        ] == 4
        assert first.provenance[
            orchestrator.DETECTION_EFFECTIVE_PARALLELISM_KEY
        ] == 2
        assert first.provenance["resource_units"] == 2
        assert second.executor is None

        assert orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        ) == []


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


def test_completed_detection_shards_queue_a_cpu_finalizer_through_scheduler(
    stage_sessions,
    monkeypatch,
):
    run_id = "6" * 32
    _add_detection_run(stage_sessions, run_id, width=55_000, height=55_000, organization_id="acme-survey")
    settings = _settings(
        detection_fanout_enabled=True,
        detection_environment=(("SAM3_MODEL_ID", "facebook/sam3"),),
        detection_secret_environment=(
            orchestrator.SecretEnvironment("HF_TOKEN", "hf-token", "HF_TOKEN"),
        ),
    )
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
                    result_key=content_addressed_blob_key(checksum, organization_id="acme-survey"),
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
    assert client.created == []
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.status == "queued"
        assert run.executor is None
        assert run.job_name is None
        assert run.resource_class == "cpu-standard"
        assert run.current_step == "DETECTION_FINALIZING"
        assert run.provenance[orchestrator.DETECTION_PHASE_KEY] == "finalizer"
        assert run.provenance["detection_shard_job_name"] == shard_job_name
        assert run.provenance[
            orchestrator.DETECTION_INFERENCE_RESOURCE_CLASS_KEY
        ] == "gpu-standard"

        finalizers = orchestrator.reserve_ready_jobs(
            session,
            settings,
            datetime.now(UTC),
        )

    assert len(finalizers) == 1
    finalizer = finalizers[0]
    assert finalizer.job_name == finalizer_name
    assert finalizer.request.resource_class == "cpu-standard"
    assert finalizer.config.indexed is None
    assert finalizer.config.node_selector == ()
    assert finalizer.config.tolerations == ()
    assert finalizer.config.secret_environment == ()
    orchestrator.dispatch_reserved_jobs(
        client,
        finalizers,
        settings.maximum_dispatch_attempts,
    )

    assert client.created[0]["metadata"]["name"] == finalizer_name
    assert "completionMode" not in client.created[0]["spec"]
    pod = client.created[0]["spec"]["template"]["spec"]
    assert "nodeSelector" not in pod
    assert "tolerations" not in pod
    assert "runtimeClassName" not in pod
    container = pod["containers"][0]
    assert "nvidia.com/gpu" not in container["resources"]["limits"]
    environment = {
        item["name"]: item.get("value")
        for item in container["env"]
    }
    assert environment["DRONEAI_DETECTION_EXECUTION_MODE"] == "finalizer"
    assert environment["DRONEAI_STAGE_RUN_ID"] == run_id
    assert environment["DRONEAI_RESOURCE_CLASS"] == "cpu-standard"
    assert "SAM3_MODEL_ID" not in environment
    assert "HF_TOKEN" not in environment
    with stage_sessions() as session:
        run = session.query(MissionStageRun).filter_by(run_id=run_id).one()
        assert run.status == "queued"
        assert run.job_name == finalizer_name
        assert run.executor == orchestrator.EXECUTOR_NAME
        assert run.provenance["resource_class"] == "cpu-standard"
        assert run.provenance["resource_units"] == 1
        assert run.provenance["gpu_architecture"] is None


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
        organization_id="acme-survey",
        workspace_prefix="organizations/acme-survey/missions/mission-model-scope",
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
    finalizer = orchestrator._reserved_job(
        SimpleNamespace(
            run_id="f" * 32,
            stage="detection",
            resource_class="gpu-high-memory",
            provenance={
                orchestrator.DETECTION_PHASE_KEY: "finalizer",
            },
        ),
        mission,
        settings,
    )

    assert detection.config.environment == model_environment
    assert detection.config.secret_environment == model_secret
    assert reconstruction.config.environment == ()
    assert reconstruction.config.secret_environment == ()
    assert finalizer.request.resource_class == "cpu-standard"
    assert finalizer.config.environment == (
        ("DRONEAI_DETECTION_EXECUTION_MODE", "finalizer"),
    )
    assert finalizer.config.secret_environment == ()
    assert finalizer.config.tolerations == ()


def test_job_create_conflict_accepts_only_the_same_reserved_manifest(stage_sessions):
    run_id = "1" * 32
    _add_run(stage_sessions, "mission-conflict", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )[0]
    expected = orchestrator.build_stage_job(reserved.request, reserved.config)
    expected["spec"]["suspend"] = False
    expected["metadata"]["labels"]["batch.kubernetes.io/controller-uid"] = "server-default"

    orchestrator._create_job(ConflictJobClient(expected), reserved)


def test_job_create_conflict_rejects_an_existing_job_with_different_spec(
    stage_sessions,
):
    run_id = "2" * 32
    _add_run(stage_sessions, "mission-conflict-mismatch", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )[0]
    existing = orchestrator.build_stage_job(reserved.request, reserved.config)
    existing["spec"]["template"]["spec"]["containers"][0]["image"] = (
        "registry.example/untrusted@sha256:" + "f" * 64
    )

    with pytest.raises(RuntimeError, match="conflicts with the reserved"):
        orchestrator._create_job(ConflictJobClient(existing), reserved)


def test_job_create_conflict_rejects_a_forged_annotation(stage_sessions):
    run_id = "3" * 32
    _add_run(stage_sessions, "mission-conflict-forged", "owner-a", run_id)
    with stage_sessions() as session:
        reserved = orchestrator.reserve_ready_jobs(
            session,
            _settings(),
            datetime.now(UTC),
        )[0]
    expected = orchestrator.build_stage_job(reserved.request, reserved.config)
    existing = json.loads(json.dumps(expected))
    existing["spec"]["backoffLimit"] = 6

    with pytest.raises(RuntimeError, match="conflicts with the reserved"):
        orchestrator._create_job(ConflictJobClient(existing), reserved)
