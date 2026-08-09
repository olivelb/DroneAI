from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.database import DatasetUploadFile, DatasetUploadSession

uploads = importlib.import_module("app4-dashboard.api.dataset_uploads")
security = importlib.import_module("app4-dashboard.api.security")
datasets_router = importlib.import_module("app4-dashboard.api.routers.datasets")


@pytest.fixture
def upload_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DatasetUploadSession.__table__.create(engine)
    DatasetUploadFile.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


@pytest.fixture
def fake_storage(monkeypatch):
    created: dict[str, str] = {}
    completed: dict[str, int] = {}
    objects: dict[str, bytes] = {}

    monkeypatch.setattr(uploads.storage, "list_objects", lambda _prefix: [])

    def create(key, **_kwargs):
        upload_id = f"multipart-{len(created) + 1}"
        created[key] = upload_id
        return upload_id

    def complete(key, _upload_id, parts):
        assert [part["PartNumber"] for part in parts] == list(
            range(1, len(parts) + 1)
        )
        return {
            "key": key,
            "size": completed[key],
            "etag": f'"etag-{len(parts)}"',
        }

    monkeypatch.setattr(uploads.storage, "create_multipart_upload", create)
    monkeypatch.setattr(uploads.storage, "complete_multipart_upload", complete)
    monkeypatch.setattr(
        uploads.storage,
        "get_presigned_upload_part_url",
        lambda key, upload_id, part, **_kwargs: (
            f"https://objects.example/{key}?uploadId={upload_id}&partNumber={part}"
        ),
    )
    monkeypatch.setattr(
        uploads.storage,
        "put_object",
        lambda key, body: objects.__setitem__(key, body),
    )
    monkeypatch.setattr(
        uploads.storage,
        "get_object_size",
        lambda key: len(objects[key]),
    )
    monkeypatch.setattr(uploads.storage, "abort_multipart_upload", lambda *_args: None)
    monkeypatch.setattr(uploads.storage, "delete_object", lambda *_args: None)
    return created, completed, objects


def test_direct_upload_is_durable_presigned_and_atomically_finalized(
    upload_session,
    fake_storage,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_UPLOAD_PART_BYTES", str(5 * 1024 * 1024))
    created, completed, objects = fake_storage
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


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_legacy_proxied_upload_is_disabled_outside_development(
    environment,
    monkeypatch,
):
    monkeypatch.setenv("DRONEAI_ENV", environment)

    with pytest.raises(HTTPException) as error:
        datasets_router.upload_dataset_batch("dataset", [])

    assert error.value.status_code == 404
