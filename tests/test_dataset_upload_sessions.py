from __future__ import annotations

import importlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import (
    Dataset,
    DatasetUploadFile,
    DatasetUploadSession,
    Mission,
    MissionArtifact,
    OrganizationSaasPolicy,
    OrganizationUsageEvent,
)

uploads = importlib.import_module("app4-dashboard.api.dataset_uploads")
security = importlib.import_module("app4-dashboard.api.security")
datasets_router = importlib.import_module("app4-dashboard.api.routers.datasets")


@pytest.fixture
def upload_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DatasetUploadSession.__table__.create(engine)
    Dataset.__table__.create(engine)
    DatasetUploadFile.__table__.create(engine)
    Mission.__table__.create(engine)
    MissionArtifact.__table__.create(engine)
    OrganizationSaasPolicy.__table__.create(engine)
    OrganizationUsageEvent.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def fake_storage(monkeypatch):
    created: dict[str, str] = {}
    completed: dict[str, int] = {}
    objects: dict[str, bytes] = {}
    object_info: dict[str, dict] = {}
    multipart: dict[str, dict] = {}
    state = {
        "sequence": 0,
        "complete_calls": 0,
        "abort_calls": [],
        "manifest_puts": [],
    }

    monkeypatch.setattr(uploads.storage, "list_objects", lambda _prefix: [])

    def create(key, **kwargs):
        state["sequence"] += 1
        upload_id = f"multipart-{state['sequence']}"
        created[key] = upload_id
        multipart[upload_id] = {
            "key": key,
            "metadata": kwargs.get("metadata") or {},
            "content_type": kwargs.get("content_type") or "",
        }
        return upload_id

    def complete(key, upload_id, parts):
        assert [part["PartNumber"] for part in parts] == list(
            range(1, len(parts) + 1)
        )
        state["complete_calls"] += 1
        upload = multipart.pop(upload_id)
        assert upload["key"] == key
        object_info[key] = {
            "key": key,
            "size": completed[key],
            "etag": f'"etag-{len(parts)}"',
            "content_type": upload["content_type"],
            "metadata": upload["metadata"],
        }
        return {
            "key": key,
            "size": completed[key],
            "etag": f'"etag-{len(parts)}"',
        }

    def abort(key, upload_id):
        state["abort_calls"].append((key, upload_id))
        multipart.pop(upload_id, None)

    def delete(key):
        object_info.pop(key, None)
        objects.pop(key, None)

    monkeypatch.setattr(uploads.storage, "create_multipart_upload", create)
    monkeypatch.setattr(
        uploads.storage,
        "list_multipart_uploads",
        lambda key: [
            upload_id
            for upload_id, upload in multipart.items()
            if upload["key"] == key
        ],
    )
    monkeypatch.setattr(uploads.storage, "complete_multipart_upload", complete)
    monkeypatch.setattr(
        uploads.storage,
        "get_object_info",
        lambda key: object_info.get(key),
    )
    monkeypatch.setattr(
        uploads.storage,
        "get_presigned_upload_part_url",
        lambda key, upload_id, part, **_kwargs: (
            f"https://objects.example/{key}?uploadId={upload_id}&partNumber={part}"
        ),
    )
    def put(key, body):
        objects[key] = body
        state["manifest_puts"].append((key, body))

    monkeypatch.setattr(uploads.storage, "put_object", put)
    monkeypatch.setattr(
        uploads.storage,
        "get_object_size",
        lambda key: len(objects[key]),
    )
    monkeypatch.setattr(uploads.storage, "abort_multipart_upload", abort)
    monkeypatch.setattr(uploads.storage, "delete_object", delete)
    return {
        "created": created,
        "completed": completed,
        "objects": objects,
        "object_info": object_info,
        "multipart": multipart,
        "state": state,
    }


def test_direct_upload_is_durable_presigned_and_atomically_finalized(
    upload_session,
    fake_storage,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_UPLOAD_PART_BYTES", str(5 * 1024 * 1024))
    created = fake_storage["created"]
    completed = fake_storage["completed"]
    objects = fake_storage["objects"]
    principal = security.Principal("operator-1", "operator")
    request = uploads.UploadSessionRequest(
        dataset_name="Quarry survey",
        files=[
            {
                "name": "DJI_0001.JPG",
                "size": 6 * 1024 * 1024,
                "content_type": "image/jpeg",
            },
            {
                "name": "DJI_0002.JPG",
                "size": 1024,
                "content_type": "image/jpeg",
            },
        ],
    )

    response = uploads.create_upload_session(upload_session, request, principal)
    upload_session.commit()

    assert response["dataset"] == "Quarry_survey"
    assert response["part_size"] == 5 * 1024 * 1024
    assert [item["total_parts"] for item in response["files"]] == [2, 1]
    assert set(created) == {
        "datasets/Quarry_survey/DJI_0001.JPG",
        "datasets/Quarry_survey/DJI_0002.JPG",
    }

    for descriptor in response["files"]:
        completed[descriptor["s3_key"]] = descriptor["size"]
        signed = uploads.create_part_url(
            upload_session,
            response["session_id"],
            descriptor["file_id"],
            1,
            principal,
        )
        assert signed["method"] == "PUT"
        assert "partNumber=1" in signed["url"]
        parts = [
            {"part_number": number, "etag": f'"part-{number}"'}
            for number in range(1, descriptor["total_parts"] + 1)
        ]
        result = uploads.complete_upload_file(
            upload_session,
            response["session_id"],
            descriptor["file_id"],
            uploads.CompleteUploadFileRequest(parts=parts),
            principal,
        )
        assert result["status"] == "completed"

    final = uploads.finalize_upload_session(
        upload_session,
        response["session_id"],
        principal,
    )
    upload_session.commit()

    assert final == {
        "upload_id": response["session_id"],
        "dataset": "Quarry_survey",
        "total": 2,
        "completed": 2,
        "failed": 0,
        "status": "done",
        "manifest_s3_key": "datasets/Quarry_survey/dataset-manifest.json",
    }
    assert final["manifest_s3_key"] in objects
    persisted = upload_session.query(DatasetUploadSession).one()
    assert persisted.status == "completed"
    dataset = upload_session.query(Dataset).one()
    assert dataset.owner_subject == "operator-1"
    assert dataset.prefix == "datasets/Quarry_survey"
    assert dataset.status == "ready"
    assert dataset.file_count == 2
    assert dataset.image_count == 2
    usage_event = upload_session.query(OrganizationUsageEvent).filter_by(
        action="storage_reserved"
    ).one()
    assert usage_event.quantity == 6 * 1024 * 1024 + 1024


def test_direct_upload_rejects_cross_principal_access(upload_session, fake_storage):
    owner = security.Principal("operator-1", "operator")
    stranger = security.Principal("operator-2", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="private",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        owner,
    )
    upload_session.commit()

    with pytest.raises(HTTPException) as error:
        uploads.create_part_url(
            upload_session,
            response["session_id"],
            response["files"][0]["file_id"],
            1,
            stranger,
        )

    assert error.value.status_code == 404


def test_direct_upload_requires_every_part_once(upload_session, fake_storage, monkeypatch):
    monkeypatch.setenv("DRONEAI_UPLOAD_PART_BYTES", str(5 * 1024 * 1024))
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="parts",
            files=[{"name": "large.jpg", "size": 6 * 1024 * 1024}],
        ),
        principal,
    )
    descriptor = response["files"][0]

    with pytest.raises(HTTPException) as error:
        uploads.complete_upload_file(
            upload_session,
            response["session_id"],
            descriptor["file_id"],
            uploads.CompleteUploadFileRequest(
                parts=[{"part_number": 1, "etag": '"part-1"'}]
            ),
            principal,
        )

    assert error.value.status_code == 400


def test_direct_upload_applies_existing_batch_quotas(
    upload_session,
    fake_storage,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_UPLOAD_MAX_FILES", "1")
    principal = security.Principal("operator-1", "operator")

    with pytest.raises(HTTPException) as error:
        uploads.create_upload_session(
            upload_session,
            uploads.UploadSessionRequest(
                dataset_name="too-many",
                files=[
                    {"name": "one.jpg", "size": 1},
                    {"name": "two.jpg", "size": 1},
                ],
            ),
            principal,
        )

    assert error.value.status_code == 413


def test_direct_upload_enforces_organization_storage_quota(
    upload_session,
    fake_storage,
):
    upload_session.add(
        OrganizationSaasPolicy(
            organization_id="legacy-unassigned",
            storage_limit_bytes=1_000,
            version=1,
            created_by="platform-support",
            updated_by="platform-support",
        )
    )
    upload_session.commit()

    with pytest.raises(HTTPException) as error:
        uploads.create_upload_session(
            upload_session,
            uploads.UploadSessionRequest(
                dataset_name="over-organization-quota",
                files=[{"name": "image.jpg", "size": 1_001}],
            ),
            security.Principal("operator-1", "operator"),
        )

    assert error.value.status_code == 413
    assert error.value.detail == {
        "message": "Organization storage quota exceeded",
        "current_bytes": 0,
        "requested_bytes": 1_001,
        "limit_bytes": 1_000,
    }
    assert upload_session.query(DatasetUploadSession).count() == 0


def _upload_record(dataset_name: str, *, status: str = "uploading") -> DatasetUploadSession:
    return DatasetUploadSession(
        dataset_name=dataset_name,
        status=status,
        total_bytes=1,
        file_count=1,
        part_size=uploads.MIN_PART_BYTES,
        created_by="operator-1",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_active_dataset_name_is_reserved_by_a_partial_unique_index(upload_session):
    first = _upload_record("same-name")
    upload_session.add(first)
    upload_session.commit()

    upload_session.add(_upload_record("same-name", status="failed"))
    with pytest.raises(IntegrityError):
        upload_session.flush()
    upload_session.rollback()

    first = upload_session.query(DatasetUploadSession).one()
    first.status = "completed"
    upload_session.commit()
    upload_session.add(_upload_record("same-name"))
    upload_session.flush()


def test_expired_cleanup_query_uses_skip_locked(upload_session):
    statement = uploads._expired_upload_query(
        upload_session,
        datetime.now(UTC),
    ).statement
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "ORDER BY dataset_upload_sessions.expires_at" in sql


def test_missing_multipart_upload_is_an_idempotent_abort(
    upload_session,
    fake_storage,
    monkeypatch,
):
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="already-aborted",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        principal,
    )
    record = upload_session.query(DatasetUploadSession).filter(
        DatasetUploadSession.session_id == response["session_id"]
    ).one()

    class MissingUploadError(RuntimeError):
        response = {"Error": {"Code": "NoSuchUpload"}}

    monkeypatch.setattr(
        uploads.storage,
        "abort_multipart_upload",
        lambda *_args: (_ for _ in ()).throw(MissingUploadError()),
    )

    uploads._abort_record(record)

    assert record.status == "aborted"
    assert record.files[0].status == "aborted"


def test_initialization_recovers_orphan_after_database_commit_failure(
    upload_session,
    fake_storage,
    monkeypatch,
):
    principal = security.Principal("operator-1", "operator")
    request = uploads.UploadSessionRequest(
        dataset_name="recover-create",
        files=[{"name": "image.jpg", "size": 1024}],
    )
    original_commit = upload_session.commit
    commit_calls = 0

    def fail_handle_commit_once():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("database unavailable after S3 create")
        original_commit()

    original_abort = uploads.storage.abort_multipart_upload
    abort_calls = 0

    def fail_immediate_abort_once(key, upload_id):
        nonlocal abort_calls
        abort_calls += 1
        if abort_calls == 1:
            raise RuntimeError("abort temporarily unavailable")
        original_abort(key, upload_id)

    monkeypatch.setattr(upload_session, "commit", fail_handle_commit_once)
    monkeypatch.setattr(
        uploads.storage,
        "abort_multipart_upload",
        fail_immediate_abort_once,
    )

    with pytest.raises(HTTPException) as error:
        uploads.create_upload_session(upload_session, request, principal)

    assert error.value.status_code == 502
    persisted = upload_session.query(DatasetUploadSession).one()
    assert persisted.status == "initializing"
    assert persisted.files[0].status == "initializing"
    assert list(fake_storage["multipart"]) == ["multipart-1"]

    monkeypatch.setattr(upload_session, "commit", original_commit)
    response = uploads.create_upload_session(upload_session, request, principal)

    assert response["status"] == "uploading"
    assert response["files"][0]["status"] == "uploading"
    assert list(fake_storage["multipart"]) == ["multipart-2"]
    assert fake_storage["state"]["abort_calls"] == [
        ("datasets/recover-create/image.jpg", "multipart-1")
    ]


def test_completion_retry_adopts_object_after_database_commit_failure(
    upload_session,
    fake_storage,
    monkeypatch,
):
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="recover-complete",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        principal,
    )
    descriptor = response["files"][0]
    fake_storage["completed"][descriptor["s3_key"]] = descriptor["size"]
    request = uploads.CompleteUploadFileRequest(
        parts=[{"part_number": 1, "etag": '"part-1"'}]
    )
    original_commit = upload_session.commit
    commit_calls = 0

    def fail_completed_state_once():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("database unavailable after S3 complete")
        original_commit()

    monkeypatch.setattr(upload_session, "commit", fail_completed_state_once)
    with pytest.raises(HTTPException) as error:
        uploads.complete_upload_file(
            upload_session,
            response["session_id"],
            descriptor["file_id"],
            request,
            principal,
        )
    assert error.value.status_code == 502
    upload_session.rollback()
    upload_session.expire_all()
    assert upload_session.query(DatasetUploadFile).one().status == "completing"
    assert fake_storage["state"]["complete_calls"] == 1

    monkeypatch.setattr(upload_session, "commit", original_commit)
    recovered = uploads.complete_upload_file(
        upload_session,
        response["session_id"],
        descriptor["file_id"],
        request,
        principal,
    )

    assert recovered["status"] == "completed"
    assert upload_session.query(DatasetUploadFile).one().status == "completed"
    assert fake_storage["state"]["complete_calls"] == 1


def test_finalization_retry_reuses_durable_timestamp_after_commit_failure(
    upload_session,
    fake_storage,
    monkeypatch,
):
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="recover-finalize",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        principal,
    )
    descriptor = response["files"][0]
    fake_storage["completed"][descriptor["s3_key"]] = descriptor["size"]
    uploads.complete_upload_file(
        upload_session,
        response["session_id"],
        descriptor["file_id"],
        uploads.CompleteUploadFileRequest(
            parts=[{"part_number": 1, "etag": '"part-1"'}]
        ),
        principal,
    )
    original_commit = upload_session.commit
    commit_calls = 0

    def fail_catalog_commit_once():
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("database unavailable after manifest publish")
        original_commit()

    monkeypatch.setattr(upload_session, "commit", fail_catalog_commit_once)
    with pytest.raises(HTTPException) as error:
        uploads.finalize_upload_session(
            upload_session,
            response["session_id"],
            principal,
        )
    assert error.value.status_code == 502
    upload_session.rollback()
    upload_session.expire_all()
    persisted = upload_session.query(DatasetUploadSession).one()
    assert persisted.status == "finalizing"
    assert upload_session.query(Dataset).count() == 0
    first_manifest = fake_storage["state"]["manifest_puts"][0][1]

    monkeypatch.setattr(upload_session, "commit", original_commit)
    recovered = uploads.finalize_upload_session(
        upload_session,
        response["session_id"],
        principal,
    )

    assert recovered["status"] == "done"
    assert upload_session.query(DatasetUploadSession).one().status == "completed"
    assert upload_session.query(Dataset).count() == 1
    assert fake_storage["state"]["manifest_puts"][1][1] == first_manifest


def test_idempotent_create_retry_only_returns_files_still_uploading(
    upload_session,
    fake_storage,
):
    principal = security.Principal("operator-1", "operator")
    request = uploads.UploadSessionRequest(
        dataset_name="resume-batch",
        files=[
            {"name": "one.jpg", "size": 1024},
            {"name": "two.jpg", "size": 2048},
        ],
    )
    response = uploads.create_upload_session(upload_session, request, principal)
    completed = response["files"][0]
    fake_storage["completed"][completed["s3_key"]] = completed["size"]
    uploads.complete_upload_file(
        upload_session,
        response["session_id"],
        completed["file_id"],
        uploads.CompleteUploadFileRequest(
            parts=[{"part_number": 1, "etag": '"part-1"'}]
        ),
        principal,
    )

    resumed = uploads.create_upload_session(upload_session, request, principal)

    assert resumed["session_id"] == response["session_id"]
    assert [item["name"] for item in resumed["files"]] == ["two.jpg"]
    assert fake_storage["state"]["sequence"] == 2


def test_abort_refuses_uncertain_completion_and_finalization(
    upload_session,
    fake_storage,
):
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="no-destructive-race",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        principal,
    )
    record = upload_session.query(DatasetUploadSession).one()
    record.files[0].status = "completing"
    record.files[0].completed_parts = [{"part_number": 1, "etag": '"part-1"'}]
    upload_session.commit()

    with pytest.raises(HTTPException) as completing_error:
        uploads.abort_upload_session(
            upload_session,
            response["session_id"],
            principal,
        )
    assert completing_error.value.status_code == 409

    record = upload_session.query(DatasetUploadSession).one()
    record.files[0].status = "completed"
    upload_session.commit()
    with pytest.raises(HTTPException) as resumable_error:
        uploads.abort_upload_session(
            upload_session,
            response["session_id"],
            principal,
        )
    assert resumable_error.value.status_code == 409

    record = upload_session.query(DatasetUploadSession).one()
    record.status = "finalizing"
    record.completed_at = datetime.now(UTC)
    upload_session.commit()
    with pytest.raises(HTTPException) as finalizing_error:
        uploads.abort_upload_session(
            upload_session,
            response["session_id"],
            principal,
        )
    assert finalizing_error.value.status_code == 409


def test_pending_recovery_query_uses_skip_locked(upload_session):
    statement = uploads._pending_upload_query(upload_session).statement
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "dataset_upload_files.status = 'completing'" in sql


def test_background_reconciler_adopts_completion_and_finishes_catalog(
    upload_session,
    fake_storage,
    monkeypatch,
):
    principal = security.Principal("operator-1", "operator")
    response = uploads.create_upload_session(
        upload_session,
        uploads.UploadSessionRequest(
            dataset_name="background-recovery",
            files=[{"name": "image.jpg", "size": 1024}],
        ),
        principal,
    )
    record = upload_session.query(DatasetUploadSession).one()
    file_record = record.files[0]
    file_record.status = "completing"
    file_record.completed_parts = [{"part_number": 1, "etag": '"part-1"'}]
    upload_session.commit()
    fake_storage["completed"][str(file_record.s3_key)] = int(file_record.size_bytes)
    uploads.storage.complete_multipart_upload(
        str(file_record.s3_key),
        str(file_record.multipart_upload_id),
        [{"PartNumber": 1, "ETag": '"part-1"'}],
    )

    @contextmanager
    def local_session():
        yield upload_session

    monkeypatch.setattr(uploads, "get_session", local_session)
    assert uploads.reconcile_pending_uploads() == 1
    assert upload_session.query(DatasetUploadFile).one().status == "completed"

    record = upload_session.query(DatasetUploadSession).one()
    record.status = "finalizing"
    record.completed_at = datetime.now(UTC)
    upload_session.commit()
    assert uploads.reconcile_pending_uploads() == 1

    assert upload_session.query(DatasetUploadSession).one().status == "completed"
    assert upload_session.query(Dataset).one().name == "background-recovery"
    assert response["session_id"] == str(record.session_id)


@pytest.mark.parametrize("environment", ["development", "staging", "production"])
def test_legacy_proxied_upload_is_disabled(
    environment,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", environment)

    with pytest.raises(HTTPException) as error:
        datasets_router.upload_dataset_batch()

    assert error.value.status_code == 404
