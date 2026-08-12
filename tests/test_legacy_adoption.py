from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from shared import legacy_adoption_execution
from shared.database import (
    AIAnalysisRun,
    Dataset,
    DatasetUploadFile,
    DatasetUploadSession,
    InboxEvent,
    Mission,
    MissionArtifact,
    MissionStageRun,
    Organization,
    OrganizationMember,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
    OutboxEvent,
)
from shared.legacy_adoption import build_adoption_plan
from shared.legacy_adoption_execution import apply_adoption_plan
from tools.adopt_legacy_storage import main as adoption_cli


class FakeAdoptionStore:
    def __init__(self, objects: Mapping[str, bytes]):
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {
            key: (bytes(value), {}) for key, value in objects.items()
        }
        self.copy_calls = 0

    def list_objects(self, prefix: str) -> list[str]:
        return sorted(key for key in self.objects if key.startswith(prefix))

    def object_info(self, key: str) -> Mapping[str, object] | None:
        entry = self.objects.get(key)
        if entry is None:
            return None
        data, metadata = entry
        return {
            "key": key,
            "size": len(data),
            "etag": hashlib.sha256(data).hexdigest(),
            "content_type": "application/octet-stream",
            "metadata": dict(metadata),
        }

    def read_bytes(self, key: str) -> bytes:
        return self.objects[key][0]

    def copy(self, source_key: str, target_key: str) -> Mapping[str, object]:
        source = self.object_info(source_key)
        assert source is not None
        source_digest = hashlib.sha256(source_key.encode()).hexdigest()
        source_etag = str(source["etag"])
        existing = self.object_info(target_key)
        if existing is not None:
            metadata = existing["metadata"]
            assert isinstance(metadata, dict)
            if (
                int(existing["size"]) != int(source["size"])
                or metadata.get("source-key") != source_digest
                or metadata.get("source-etag") != source_etag
            ):
                raise OSError(f"target conflicts: {target_key}")
            return {"key": target_key, "size": source["size"], "reused": True}
        self.copy_calls += 1
        self.objects[target_key] = (
            self.objects[source_key][0],
            {"source-key": source_digest, "source-etag": source_etag},
        )
        return {"key": target_key, "size": source["size"], "reused": False}

    def put_bytes(self, key: str, data: bytes) -> Mapping[str, object]:
        digest = hashlib.sha256(data).hexdigest()
        existing = self.objects.get(key)
        if existing is not None:
            if existing != (data, {"sha256": digest}):
                raise OSError(f"control object conflicts: {key}")
            return {
                "key": key,
                "size": len(data),
                "sha256": digest,
                "reused": True,
            }
        self.objects[key] = (data, {"sha256": digest})
        return {
            "key": key,
            "size": len(data),
            "sha256": digest,
            "reused": False,
        }


def _cli_args() -> list[str]:
    return [
        "--target-organization-id",
        "tenant-a",
        "--owner-subject",
        "tenant-admin",
        "--actor-subject",
        "platform-operator",
        "--dataset",
        "legacy-images",
    ]


def test_apply_cli_requires_the_reviewed_run_id() -> None:
    with pytest.raises(ValueError, match="dry-run --run-id"):
        adoption_cli(
            [
                *_cli_args(),
                "--apply",
                "--confirm-plan-checksum",
                "a" * 64,
            ]
        )


def test_dry_run_cli_rejects_apply_only_checksum() -> None:
    with pytest.raises(ValueError, match="valid only with --apply"):
        adoption_cli([*_cli_args(), "--confirm-plan-checksum", "a" * 64])


@pytest.fixture
def adoption_database(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'adoption.db'}")
    for table in (
        Organization.__table__,
        OrganizationMember.__table__,
        OrganizationSaasPolicy.__table__,
        OrganizationUsageEvent.__table__,
        InboxEvent.__table__,
        OutboxEvent.__table__,
        DatasetUploadSession.__table__,
        DatasetUploadFile.__table__,
        Dataset.__table__,
        Mission.__table__,
        AIAnalysisRun.__table__,
        MissionStageRun.__table__,
        MissionArtifact.__table__,
    ):
        table.create(engine)

    @contextmanager
    def session_factory() -> Iterator[Session]:
        with Session(engine, expire_on_commit=False) as session:
            try:
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    with session_factory() as session:
        session.add_all(
            [
                Organization(
                    id="legacy-unassigned",
                    display_name="Legacy",
                    status="active",
                    created_by="migration",
                    updated_by="migration",
                ),
                Organization(
                    id="tenant-a",
                    display_name="Tenant A",
                    status="active",
                    created_by="operator",
                    updated_by="operator",
                ),
                OrganizationMember(
                    organization_id="tenant-a",
                    subject="tenant-admin",
                    role="admin",
                    status="active",
                    created_by="operator",
                    updated_by="operator",
                ),
            ]
        )
        session.flush()
        session.add(
            Dataset(
                name="legacy-images",
                organization_id="legacy-unassigned",
                owner_subject="legacy-owner",
                prefix="datasets/legacy-images",
                status="ready",
                manifest_s3_key=(
                    "datasets/legacy-images/dataset-manifest.json"
                ),
                file_count=1,
                image_count=1,
                total_bytes=4,
                ready_at=datetime.now(UTC),
            )
        )
    return session_factory


def _store() -> FakeAdoptionStore:
    manifest = {
        "schema_version": 1,
        "upload_session_id": "legacy-session",
        "dataset": "legacy-images",
        "created_by": "legacy-owner",
        "organization_id": "legacy-unassigned",
        "completed_at": datetime.now(UTC).isoformat(),
        "files": [
            {
                "name": "image.jpg",
                "s3_key": "datasets/legacy-images/image.jpg",
                "size": 4,
                "etag": "legacy-etag",
            }
        ],
    }
    return FakeAdoptionStore(
        {
            "datasets/legacy-images/image.jpg": b"jpeg",
            "datasets/legacy-images/dataset-manifest.json": json.dumps(
                manifest,
                sort_keys=True,
            ).encode(),
        }
    )


def _plan(session_factory, store, *, run_id="8fefb73b-8445-420e-a771-d198c8dd9d20"):
    with session_factory() as session:
        return build_adoption_plan(
            session,
            target_organization_id="tenant-a",
            owner_subject="tenant-admin",
            actor_subject="platform-operator",
            store=store,
            dataset_names=["legacy-images"],
            run_id=run_id,
        )


def test_dry_run_inventories_without_database_or_storage_mutation(
    adoption_database,
):
    store = _store()
    plan = _plan(adoption_database, store)

    assert plan.source_object_count == 2
    assert plan.target_write_bytes > 0
    assert plan.logical_usage_bytes == 4
    assert plan.public_summary(apply=False)["source_retained"] is True
    assert not any(key.startswith("organizations/") for key in store.objects)
    with adoption_database() as session:
        dataset = session.query(Dataset).one()
        assert dataset.organization_id == "legacy-unassigned"
        assert session.query(OrganizationUsageEvent).count() == 0


def test_plan_rejects_unsafe_legacy_object_keys(adoption_database):
    store = _store()
    store.objects["datasets/legacy-images/../escaped.jpg"] = (b"evil", {})

    with pytest.raises(ValueError, match="unsafe adoption key"):
        _plan(adoption_database, store)


def test_plan_rejects_unmanifested_dataset_objects(adoption_database):
    store = _store()
    store.objects["datasets/legacy-images/orphan.bin"] = (b"orphan", {})

    with pytest.raises(ValueError, match="S3 inventory is inconsistent"):
        _plan(adoption_database, store)


def test_plan_rejects_retryable_failed_inbox_work(adoption_database):
    vol_id = "legacy-with-retry"
    with adoption_database() as session:
        session.add(
            Mission(
                vol_id=vol_id,
                organization_id="legacy-unassigned",
                owner_subject="legacy-owner",
                workspace_prefix=f"missions/{vol_id}",
                status="completed",
            )
        )
        session.add(
            InboxEvent(
                consumer_group="integration",
                event_id="failed-event",
                event_type="status",
                payload={"vol_id": vol_id},
                status="failed",
            )
        )

    with adoption_database() as session:
        with pytest.raises(ValueError, match="active inbox"):
            build_adoption_plan(
                session,
                target_organization_id="tenant-a",
                owner_subject="tenant-admin",
                actor_subject="platform-operator",
                store=_store(),
                mission_ids=[vol_id],
            )


def test_plan_rejects_upload_session_manifest_mismatch(adoption_database):
    with adoption_database() as session:
        upload = DatasetUploadSession(
            dataset_name="legacy-images",
            organization_id="legacy-unassigned",
            status="completed",
            total_bytes=4,
            file_count=1,
            part_size=5 * 1024 * 1024,
            created_by="legacy-owner",
            expires_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        session.add(upload)
        session.flush()
        session.add(
            DatasetUploadFile(
                upload_session_id=upload.id,
                filename="other.jpg",
                s3_key="datasets/legacy-images/other.jpg",
                size_bytes=4,
                content_type="image/jpeg",
                status="completed",
            )
        )
        session.query(Dataset).one().upload_session_id = upload.id

    with pytest.raises(ValueError, match="upload files"):
        _plan(adoption_database, _store())


def test_apply_copies_verifies_audits_and_is_idempotent(adoption_database):
    store = _store()
    plan = _plan(adoption_database, store)

    apply_adoption_plan(
        plan,
        store=store,
        session_factory=adoption_database,
    )
    copy_calls = store.copy_calls
    apply_adoption_plan(
        plan,
        store=store,
        session_factory=adoption_database,
    )

    assert store.copy_calls == copy_calls == 1
    assert "datasets/legacy-images/image.jpg" in store.objects
    assert (
        "organizations/tenant-a/datasets/legacy-images/image.jpg"
        in store.objects
    )
    adopted_manifest = json.loads(
        store.objects[
            "organizations/tenant-a/datasets/legacy-images/"
            "dataset-manifest.json"
        ][0]
    )
    assert adopted_manifest["organization_id"] == "tenant-a"
    assert adopted_manifest["files"][0]["s3_key"].startswith(
        "organizations/tenant-a/datasets/legacy-images/"
    )
    with adoption_database() as session:
        dataset = session.query(Dataset).one()
        assert dataset.organization_id == "tenant-a"
        assert dataset.owner_subject == "tenant-admin"
        assert dataset.prefix == "organizations/tenant-a/datasets/legacy-images"
        assert [
            event.action
            for event in session.query(OrganizationUsageEvent)
            .order_by(OrganizationUsageEvent.id)
            .all()
        ] == [
            "legacy_adoption_started",
            "legacy_adoption_resource",
            "legacy_adoption_completed",
        ]


def test_source_change_after_plan_fails_before_database_rebind(adoption_database):
    store = _store()
    plan = _plan(
        adoption_database,
        store,
        run_id="6ff2f7c5-31ca-45e2-ae5d-08adf438a5a3",
    )
    store.objects["datasets/legacy-images/image.jpg"] = (b"evil", {})

    with pytest.raises(RuntimeError, match="source changed"):
        apply_adoption_plan(
            plan,
            store=store,
            session_factory=adoption_database,
        )

    with adoption_database() as session:
        dataset = session.query(Dataset).one()
        assert dataset.organization_id == "legacy-unassigned"
        assert [
            event.action
            for event in session.query(OrganizationUsageEvent)
            .order_by(OrganizationUsageEvent.id)
            .all()
        ] == ["legacy_adoption_started", "legacy_adoption_failed"]
    assert "datasets/legacy-images/image.jpg" in store.objects


def test_unrelated_target_object_fails_closed(adoption_database):
    store = _store()
    plan = _plan(
        adoption_database,
        store,
        run_id="34a15b05-667a-46ed-ad53-80590fd546a9",
    )
    target = "organizations/tenant-a/datasets/legacy-images/image.jpg"
    store.objects[target] = (b"unrelated", {})

    with pytest.raises(OSError, match="target conflicts"):
        apply_adoption_plan(
            plan,
            store=store,
            session_factory=adoption_database,
        )

    with adoption_database() as session:
        assert session.query(Dataset).one().organization_id == "legacy-unassigned"
        assert [
            event.action
            for event in session.query(OrganizationUsageEvent)
            .order_by(OrganizationUsageEvent.id)
            .all()
        ] == ["legacy_adoption_started", "legacy_adoption_failed"]
    assert store.objects[target][0] == b"unrelated"


def test_failure_audit_error_does_not_mask_adoption_error(
    adoption_database,
    monkeypatch,
):
    store = _store()
    plan = _plan(
        adoption_database,
        store,
        run_id="1e69ab49-a790-453b-8645-dbe5d4ec5909",
    )
    target = "organizations/tenant-a/datasets/legacy-images/image.jpg"
    store.objects[target] = (b"unrelated", {})

    def fail_to_record(*_args, **_kwargs):
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr(
        legacy_adoption_execution,
        "_record_failure",
        fail_to_record,
    )

    with pytest.raises(OSError, match="target conflicts"):
        apply_adoption_plan(
            plan,
            store=store,
            session_factory=adoption_database,
        )
