from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared.database import (
    Dataset,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    MissionStageRun,
    Organization,
    OrganizationRequestBucket,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)
from shared.organization_saas import (
    PolicyValues,
    StorageQuotaExceeded,
    check_storage_reservation,
    consume_request_quota,
    organization_usage,
    set_policy,
)
from shared.tenancy import mission_prefix
from tools.manage_organization_policy import load_policy


@pytest.fixture
def saas_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for table in (
        Organization.__table__,
        OrganizationSaasPolicy.__table__,
        OrganizationRequestBucket.__table__,
        OrganizationUsageEvent.__table__,
        DatasetUploadSession.__table__,
        Dataset.__table__,
        Mission.__table__,
        MissionStageRun.__table__,
        MissionArtifact.__table__,
    ):
        table.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(
            Organization(
                id="tenant-a",
                display_name="Tenant A",
                status="active",
                created_by="test",
                updated_by="test",
            )
        )
        session.commit()
        yield session


def test_policy_changes_are_versioned_and_append_only_in_domain(saas_session):
    first = set_policy(
        saas_session,
        organization_id="tenant-a",
        values=PolicyValues(
            storage_limit_bytes=1_000,
            concurrent_stage_runs_limit=2,
            request_rate_per_minute=60,
            request_burst=3,
            retention_days=30,
        ),
        actor_subject="platform-support",
    )
    second = set_policy(
        saas_session,
        organization_id="tenant-a",
        values=PolicyValues(
            storage_limit_bytes=2_000,
            concurrent_stage_runs_limit=3,
            request_rate_per_minute=120,
            request_burst=6,
            retention_days=60,
        ),
        actor_subject="platform-support",
    )
    saas_session.flush()

    assert first is second
    assert second.version == 2
    events = saas_session.query(OrganizationUsageEvent).order_by(
        OrganizationUsageEvent.id
    ).all()
    assert [event.action for event in events] == [
        "policy_updated",
        "policy_updated",
    ]
    assert events[-1].details["before"]["storage_limit_bytes"] == 1_000
    assert events[-1].details["after"]["storage_limit_bytes"] == 2_000


@pytest.mark.parametrize(
    "values",
    [
        {"storage_limit_bytes": 0},
        {"storage_limit_bytes": True},
        {"concurrent_stage_runs_limit": -1},
        {"request_rate_per_minute": 60},
        {"request_burst": 2},
        {"retention_days": 0},
    ],
)
def test_policy_rejects_partial_or_non_positive_limits(values):
    with pytest.raises(ValueError):
        PolicyValues(**values)


def test_operator_policy_file_requires_the_complete_integer_contract(tmp_path):
    policy_file = tmp_path / "policy.json"
    payload = {
        "storage_limit_bytes": 1_000,
        "concurrent_stage_runs_limit": 2,
        "request_rate_per_minute": 60,
        "request_burst": 4,
        "retention_days": None,
    }
    policy_file.write_text(json.dumps(payload), encoding="utf-8")

    assert load_policy(policy_file) == PolicyValues(**payload)

    payload["request_burst"] = True
    policy_file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="positive integers or null"):
        load_policy(policy_file)


@pytest.mark.parametrize("actor_subject", ["", "x" * 257])
def test_policy_rejects_an_unauditable_actor(saas_session, actor_subject):
    with pytest.raises(ValueError, match="actor subject"):
        set_policy(
            saas_session,
            organization_id="tenant-a",
            values=PolicyValues(),
            actor_subject=actor_subject,
        )


def test_storage_reservation_counts_catalog_and_active_uploads(saas_session):
    set_policy(
        saas_session,
        organization_id="tenant-a",
        values=PolicyValues(storage_limit_bytes=100),
        actor_subject="platform-support",
    )
    saas_session.add(
        Dataset(
            name="existing",
            organization_id="tenant-a",
            owner_subject="operator",
            prefix="organizations/tenant-a/datasets/existing",
            status="ready",
            manifest_s3_key=(
                "organizations/tenant-a/datasets/existing/dataset-manifest.json"
            ),
            file_count=1,
            image_count=1,
            total_bytes=60,
            ready_at=datetime.now(UTC),
        )
    )
    saas_session.flush()

    assert check_storage_reservation(
        saas_session,
        organization_id="tenant-a",
        requested_bytes=40,
    ) == 60
    with pytest.raises(StorageQuotaExceeded) as exceeded:
        check_storage_reservation(
            saas_session,
            organization_id="tenant-a",
            requested_bytes=41,
        )
    assert exceeded.value.limit_bytes == 100


def test_request_quota_is_shared_and_coalesces_throttle_audit(saas_session):
    set_policy(
        saas_session,
        organization_id="tenant-a",
        values=PolicyValues(request_rate_per_minute=60, request_burst=2),
        actor_subject="platform-support",
    )
    now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)

    assert consume_request_quota(
        saas_session,
        organization_id="tenant-a",
        actor_subject="member-a",
        now=now,
    ).retry_after_seconds is None
    assert consume_request_quota(
        saas_session,
        organization_id="tenant-a",
        actor_subject="member-a",
        now=now,
    ).retry_after_seconds is None
    denied = consume_request_quota(
        saas_session,
        organization_id="tenant-a",
        actor_subject="member-a",
        now=now,
    )
    assert denied.retry_after_seconds == pytest.approx(1.0)
    consume_request_quota(
        saas_session,
        organization_id="tenant-a",
        actor_subject="member-a",
        now=now,
    )
    assert saas_session.query(OrganizationUsageEvent).filter_by(
        action="request_throttled"
    ).count() == 1


def test_usage_separates_logical_runs_physical_units_and_retention(saas_session):
    now = datetime.now(UTC)
    set_policy(
        saas_session,
        organization_id="tenant-a",
        values=PolicyValues(retention_days=7),
        actor_subject="platform-support",
    )
    mission = Mission(
        vol_id="old-mission",
        organization_id="tenant-a",
        owner_subject="operator",
        workspace_prefix=mission_prefix("tenant-a", "old-mission"),
        status="completed",
        updated_at=now - timedelta(days=8),
    )
    saas_session.add(mission)
    saas_session.flush()
    run = MissionStageRun(
        run_id="a" * 32,
        mission_id=mission.id,
        stage="detection",
        attempt=0,
        status="running",
        executor="kubernetes-job",
        resource_class="gpu-standard",
        idempotency_key="a" * 64,
        provenance={"resource_units": 2},
    )
    saas_session.add(run)
    saas_session.flush()
    saas_session.add(
        MissionArtifact(
            artifact_id="artifact-a",
            mission_id=mission.id,
            stage_run_id=run.id,
            kind="detection",
            uri="s3://bucket/artifact",
            checksum_sha256="f" * 64,
            size_bytes=30,
        )
    )
    saas_session.flush()

    usage = organization_usage(saas_session, "tenant-a", now=now)

    assert usage.storage_bytes == 30
    assert usage.active_stage_runs == 1
    assert usage.active_stage_resource_units == 2
    assert usage.retention_eligible_missions == 1
