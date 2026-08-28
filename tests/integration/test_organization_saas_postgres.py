"""Real PostgreSQL quota serialization and retention transaction tests."""

from __future__ import annotations

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from shared.database import (
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    MissionArtifactParent,
    MissionStageRun,
    Organization,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
    get_session,
)
from shared.organization_saas import (
    PolicyValues,
    StorageQuotaExceeded,
    check_storage_reservation,
    set_policy,
)
from shared.tenancy import mission_prefix

retention = importlib.import_module("app4-dashboard.api.retention")


@pytest.mark.integration
def test_storage_reservations_serialize_on_the_organization_policy() -> None:
    suffix = uuid4().hex[:12]
    organization_id = f"quota-{suffix}"
    first_has_lock = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    with get_session() as session:
        session.add(
            Organization(
                id=organization_id,
                display_name="Quota concurrency test",
                status="active",
                created_by="integration",
                updated_by="integration",
            )
        )
        set_policy(
            session,
            organization_id=organization_id,
            values=PolicyValues(storage_limit_bytes=100),
            actor_subject="integration",
        )

    def reserve_first() -> None:
        with get_session() as session:
            check_storage_reservation(
                session,
                organization_id=organization_id,
                requested_bytes=60,
            )
            session.add(
                DatasetUploadSession(
                    dataset_name=f"first-{suffix}",
                    organization_id=organization_id,
                    status="uploading",
                    total_bytes=60,
                    file_count=1,
                    part_size=5 * 1024 * 1024,
                    created_by="integration",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            session.flush()
            first_has_lock.set()
            assert release_first.wait(5)

    def reserve_second() -> str:
        assert first_has_lock.wait(5)
        try:
            with get_session() as session:
                check_storage_reservation(
                    session,
                    organization_id=organization_id,
                    requested_bytes=60,
                )
        except StorageQuotaExceeded:
            result = "rejected"
        else:
            result = "accepted"
        second_finished.set()
        return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(reserve_first)
        second = executor.submit(reserve_second)
        assert first_has_lock.wait(5)
        assert second_finished.wait(0.2) is False
        release_first.set()
        first.result(timeout=5)
        assert second.result(timeout=5) == "rejected"


@pytest.mark.integration
def test_retention_deletes_the_postgres_graph_after_object_cleanup() -> None:
    suffix = uuid4().hex[:12]
    organization_id = f"retention-{suffix}"
    vol_id = f"retention-mission-{suffix}"
    now = datetime.now(UTC)
    with get_session() as session:
        session.add(
            Organization(
                id=organization_id,
                display_name="Retention transaction test",
                status="active",
                created_by="integration",
                updated_by="integration",
            )
        )
        set_policy(
            session,
            organization_id=organization_id,
            values=PolicyValues(retention_days=1),
            actor_subject="integration",
        )
        mission = Mission(
            vol_id=vol_id,
            organization_id=organization_id,
            owner_subject="integration",
            workspace_prefix=mission_prefix(organization_id, vol_id),
            status="completed",
            updated_at=now - timedelta(days=2),
        )
        session.add(mission)
        session.flush()
        run = MissionStageRun(
            run_id=str(uuid4()),
            mission_id=mission.id,
            stage="rasterization",
            attempt=0,
            status="succeeded",
            resource_class="gpu-standard",
            idempotency_key=uuid4().hex * 2,
        )
        session.add(run)
        session.flush()
        artifact = MissionArtifact(
            artifact_id=str(uuid4()),
            mission_id=mission.id,
            stage_run_id=run.id,
            kind="raster_product",
            uri=f"s3://drone-ai/{mission.workspace_prefix}/product.tif",
            checksum_sha256="e" * 64,
            size_bytes=123,
        )
        session.add(artifact)
        session.flush()
        mission_id = int(mission.id)
        artifact_id = int(artifact.id)
        child = MissionArtifact(
            mission_id=mission.id, stage_run_id=run.id, kind="detection_workspace",
            uri=f"s3://drone-ai/{mission.workspace_prefix}/detections.json",
            checksum_sha256="f" * 64, size_bytes=50,
        )
        session.add(child)
        session.flush()
        session.add(MissionArtifactParent(artifact_id=child.id, parent_artifact_id=artifact.id))
        child_id = int(child.id)

    deleted_prefixes: list[str] = []
    assert retention.retention_cleanup_once(
        now=now,
        delete_prefix=lambda prefix: deleted_prefixes.append(prefix) or 4,
    ) == 1

    assert deleted_prefixes == [
        f"organizations/{organization_id}/missions/{vol_id}/"
    ]
    with get_session() as session:
        assert session.get(Mission, mission_id) is None
        assert session.get(MissionArtifact, artifact_id) is None
        assert session.get(MissionArtifact, child_id) is None
        event = session.query(OrganizationUsageEvent).filter_by(
            organization_id=organization_id,
            action="retention_deleted",
        ).one()
        assert event.quantity == -173
        assert event.details["objects_deleted"] == 4
