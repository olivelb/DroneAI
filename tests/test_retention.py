from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from shared.database import (
    Mission,
    MissionArtifact,
    MissionStageRun,
    Organization,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.organization_saas import PolicyValues, set_policy
from shared.tenancy import mission_prefix

retention = importlib.import_module("app4-dashboard.api.retention")


def _retention_scope(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        Organization.__table__,
        OrganizationSaasPolicy.__table__,
        OrganizationUsageEvent.__table__,
        Mission.__table__,
        MissionStageRun.__table__,
        MissionArtifact.__table__,
    ):
        table.create(engine)
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

    monkeypatch.setattr(retention, "get_session", scope)
    with scope() as session:
        session.add(
            Organization(
                id="tenant-a",
                display_name="Tenant A",
                status="active",
                created_by="test",
                updated_by="test",
            )
        )
        set_policy(
            session,
            organization_id="tenant-a",
            values=PolicyValues(retention_days=7),
            actor_subject="platform-support",
        )
    return scope


def _mission(session, vol_id: str, *, updated_at: datetime) -> Mission:
    record = Mission(
        vol_id=vol_id,
        organization_id="tenant-a",
        owner_subject="operator",
        workspace_prefix=mission_prefix("tenant-a", vol_id),
        status="completed",
        updated_at=updated_at,
    )
    session.add(record)
    return record


def test_retention_deletes_only_expired_terminal_missions(monkeypatch):
    scope = _retention_scope(monkeypatch)
    now = datetime.now(UTC)
    with scope() as session:
        _mission(session, "expired", updated_at=now - timedelta(days=8))
        _mission(session, "recent", updated_at=now - timedelta(days=6))
    deleted_prefixes = []

    cleaned = retention.retention_cleanup_once(
        now=now,
        delete_prefix=lambda prefix: deleted_prefixes.append(prefix) or 3,
    )

    assert cleaned == 1
    assert deleted_prefixes == ["organizations/tenant-a/missions/expired/"]
    with scope() as session:
        assert [item.vol_id for item in session.query(Mission).all()] == [
            "recent"
        ]
        event = session.query(OrganizationUsageEvent).filter_by(
            action="retention_deleted"
        ).one()
        assert event.resource_id == "expired"
        assert event.details["objects_deleted"] == 3


def test_retention_failure_is_durable_and_retried_after_backoff(monkeypatch):
    scope = _retention_scope(monkeypatch)
    now = datetime.now(UTC)
    with scope() as session:
        _mission(session, "retry-me", updated_at=now - timedelta(days=8))

    def fail(_prefix: str) -> int:
        raise RuntimeError("object store unavailable")

    assert retention.retention_cleanup_once(
        now=now,
        retry_seconds=3_600,
        delete_prefix=fail,
    ) == 0
    with scope() as session:
        mission = session.query(Mission).one()
        assert mission.status == "deletion_failed"
        assert "object store unavailable" in mission.error_message
        assert session.query(OrganizationUsageEvent).filter_by(
            action="retention_failed"
        ).count() == 1

    assert retention.retention_cleanup_once(
        now=now + timedelta(seconds=3_599),
        retry_seconds=3_600,
        delete_prefix=lambda _prefix: 0,
    ) == 0
    with scope() as session:
        set_policy(
            session,
            organization_id="tenant-a",
            values=PolicyValues(),
            actor_subject="platform-support",
        )
    assert retention.retention_cleanup_once(
        now=now + timedelta(seconds=3_601),
        retry_seconds=3_600,
        delete_prefix=lambda _prefix: 0,
    ) == 1


def test_manual_deletion_waits_for_job_cleanup_and_releases_usage(monkeypatch):
    scope = _retention_scope(monkeypatch)
    now = datetime.now(UTC)
    with scope() as session:
        mission = _mission(session, "manual-delete", updated_at=now)
        mission.status = "cancelled"
        mission.current_step = "DELETION_REQUESTED"
        session.flush()
        run = MissionStageRun(
            run_id="d" * 32,
            mission_id=mission.id,
            stage="reconstruction",
            attempt=0,
            status="cancelled",
            idempotency_key="d" * 64,
            executor="kubernetes-job",
            job_name="stage-delete-me",
            completed_at=now,
        )
        session.add(run)
        session.flush()
        session.add(
            MissionArtifact(
                artifact_id="artifact-delete-me",
                mission_id=mission.id,
                stage_run_id=run.id,
                kind="reconstruction_workspace",
                uri="s3://drone-ai/delete-me",
                checksum_sha256="a" * 64,
                size_bytes=42,
            )
        )

    deleted_prefixes: list[str] = []
    assert retention.retention_cleanup_once(
        now=now,
        delete_prefix=lambda prefix: deleted_prefixes.append(prefix) or 2,
    ) == 0
    assert deleted_prefixes == []

    with scope() as session:
        run = session.query(MissionStageRun).one()
        run.provenance = {"cancellation_job_cleanup_at": now.isoformat()}

    assert retention.retention_cleanup_once(
        now=now,
        delete_prefix=lambda prefix: deleted_prefixes.append(prefix) or 2,
    ) == 1
    assert deleted_prefixes == [
        "organizations/tenant-a/missions/manual-delete/"
    ]
    with scope() as session:
        event = session.query(OrganizationUsageEvent).filter_by(
            action="storage_released"
        ).one()
        assert event.action == "storage_released"
        assert event.quantity == -42
        assert event.details["deletion_reason"] == "manual"
