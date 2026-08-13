"""Crash-safe object-storage transitions for durable dataset uploads."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from shared import storage
from shared.database import Dataset, DatasetUploadFile, DatasetUploadSession

from .dataset_upload_contracts import (
    IMAGE_SUFFIXES,
    UploadFileCompleteResponse,
    UploadFinalizeResponse,
    aware,
    completed_file_response,
    finalize_response,
    object_identity,
    record_dataset_prefix,
    stored_s3_parts,
)

logger = logging.getLogger(__name__)


def storage_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        details = response.get("Error")
        if isinstance(details, dict):
            code = details.get("Code")
            if isinstance(code, str):
                return code
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


def _record_initialization_error(
    session: Session,
    record_id: int,
    file_id: int,
    error: Exception,
) -> None:
    """Best-effort persistence after a storage or database initialization error."""

    try:
        record = cast(
            DatasetUploadSession,
            session.query(DatasetUploadSession)
            .filter(DatasetUploadSession.id == record_id)
            .with_for_update()
            .one(),
        )
        file_record = cast(
            DatasetUploadFile,
            session.query(DatasetUploadFile)
            .filter(DatasetUploadFile.id == file_id)
            .with_for_update()
            .one(),
        )
        message = str(error)[:4000]
        record.last_error = message
        file_record.last_error = message
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist multipart initialization error")


def initialize_pending_files(session: Session, record_id: int) -> None:
    """Create S3 multipart handles only after their database intent is durable."""

    while True:
        # Commits release the parent-row lock. Refresh the identity map before
        # claiming the next file so another API replica cannot leave us with a
        # stale ``initializing`` relationship and a valid handle we would abort.
        session.expire_all()
        record = cast(
            DatasetUploadSession,
            session.query(DatasetUploadSession)
            .filter(DatasetUploadSession.id == record_id)
            .with_for_update()
            .one(),
        )
        file_record = next(
            (item for item in record.files if item.status == "initializing"),
            None,
        )
        if file_record is None:
            if record.status == "initializing":
                record.status = "uploading"
                record.last_error = None
                session.commit()
            return

        created_upload_id: str | None = None
        try:
            key = str(file_record.s3_key)
            for orphan_upload_id in storage.list_multipart_uploads(key):
                try:
                    storage.abort_multipart_upload(key, orphan_upload_id)
                except Exception as error:
                    if storage_error_code(error) != "NoSuchUpload":
                        raise
            created_upload_id = storage.create_multipart_upload(
                key,
                content_type=str(file_record.content_type),
                metadata={
                    "droneai-upload-session": str(record.session_id),
                    "droneai-upload-file": str(file_record.file_id),
                    "expected-size": str(file_record.size_bytes),
                },
            )
            file_record.multipart_upload_id = created_upload_id
            file_record.status = "uploading"
            file_record.last_error = None
            session.commit()
        except Exception as error:
            failed_file_id = int(file_record.id)
            session.rollback()
            if created_upload_id is not None:
                try:
                    storage.abort_multipart_upload(key, created_upload_id)
                except Exception:
                    logger.exception(
                        "Failed to abort multipart upload after database failure"
                    )
            _record_initialization_error(
                session,
                record_id,
                failed_file_id,
                error,
            )
            raise


def _fail_file_completion(
    session: Session,
    record: DatasetUploadSession,
    file_record: DatasetUploadFile,
    message: str,
    *,
    delete_owned_object: bool = False,
) -> None:
    if delete_owned_object:
        storage.delete_object(str(file_record.s3_key))
    file_record.status = "failed"
    file_record.last_error = message[:4000]
    record.status = "failed"
    record.last_error = message[:4000]
    session.commit()
    raise HTTPException(status_code=422, detail=message)


def _persist_transient_recovery_error(
    session: Session,
    record: DatasetUploadSession,
    file_record: DatasetUploadFile,
    error: Exception,
) -> None:
    message = str(error)[:4000]
    record.last_error = message
    file_record.last_error = message
    session.commit()


def complete_file_from_intent(
    session: Session,
    record_id: int,
    file_record_id: int,
) -> UploadFileCompleteResponse | None:
    """Converge a durable ``completing`` intent with the observable S3 state."""

    record = cast(
        DatasetUploadSession,
        session.query(DatasetUploadSession)
        .filter(DatasetUploadSession.id == record_id)
        .with_for_update()
        .one(),
    )
    file_record = cast(
        DatasetUploadFile,
        session.query(DatasetUploadFile)
        .filter(DatasetUploadFile.id == file_record_id)
        .with_for_update()
        .one(),
    )
    if file_record.status == "completed":
        return completed_file_response(file_record)
    if file_record.status != "completing":
        return None

    try:
        info = storage.get_object_info(str(file_record.s3_key))
    except Exception as error:
        _persist_transient_recovery_error(session, record, file_record, error)
        raise
    if info is None:
        upload_id = str(file_record.multipart_upload_id or "")
        if not upload_id:
            _fail_file_completion(
                session,
                record,
                file_record,
                "Multipart upload ID is missing and no completed object exists",
            )
        expected_parts = stored_s3_parts(file_record)
        observed_parts = storage.list_multipart_parts(
            str(file_record.s3_key),
            upload_id,
        )
        expected_sizes = [
            min(
                int(record.part_size),
                int(file_record.size_bytes) - (index * int(record.part_size)),
            )
            for index in range(len(expected_parts))
        ]
        observed_identity = [
            (
                int(part["PartNumber"]),
                int(part["Size"]),
                str(part["ETag"]),
            )
            for part in observed_parts
        ]
        expected_identity = [
            (
                int(part["PartNumber"]),
                expected_sizes[index],
                str(part["ETag"]),
            )
            for index, part in enumerate(expected_parts)
        ]
        if observed_identity != expected_identity:
            storage.abort_multipart_upload(str(file_record.s3_key), upload_id)
            _fail_file_completion(
                session,
                record,
                file_record,
                "Provider multipart sizes or ETags do not match the upload intent",
            )
        try:
            storage.complete_multipart_upload(
                str(file_record.s3_key),
                upload_id,
                expected_parts,
            )
        except Exception as error:
            try:
                info = storage.get_object_info(str(file_record.s3_key))
            except Exception as reconciliation_error:
                _persist_transient_recovery_error(
                    session,
                    record,
                    file_record,
                    reconciliation_error,
                )
                raise reconciliation_error from error
            if info is None and storage_error_code(error) == "NoSuchUpload":
                _fail_file_completion(
                    session,
                    record,
                    file_record,
                    "Multipart upload disappeared before a completed object was visible",
                )
            if info is None:
                _persist_transient_recovery_error(session, record, file_record, error)
                raise
        if info is None:
            info = storage.get_object_info(str(file_record.s3_key))
            if info is None:
                missing_object_error = RuntimeError(
                    "S3 completed multipart upload but HEAD returned no object"
                )
                _persist_transient_recovery_error(
                    session,
                    record,
                    file_record,
                    missing_object_error,
                )
                raise missing_object_error

    owned, valid = object_identity(record, file_record, info)
    if not valid:
        _fail_file_completion(
            session,
            record,
            file_record,
            "Completed object identity or size does not match the upload intent",
            delete_owned_object=owned,
        )
    file_record.status = "completed"
    file_record.etag = str(info["etag"])
    file_record.last_error = None
    record.last_error = None
    session.commit()
    return completed_file_response(file_record)


def ensure_catalog_record(
    session: Session,
    record: DatasetUploadSession,
) -> Dataset:
    existing = cast(
        Dataset | None,
        session.query(Dataset).filter(Dataset.upload_session_id == record.id).first(),
    )
    if existing is not None:
        return existing
    completed_at = cast(datetime | None, record.completed_at)
    if completed_at is None:
        raise RuntimeError("Completed dataset upload has no completion timestamp")
    prefix = record_dataset_prefix(record)
    dataset = Dataset(
        upload_session_id=record.id,
        name=str(record.dataset_name),
        organization_id=str(record.organization_id),
        owner_subject=str(record.created_by),
        prefix=prefix,
        status="ready",
        manifest_s3_key=f"{prefix}/dataset-manifest.json",
        file_count=int(record.file_count),
        image_count=sum(
            1
            for item in record.files
            if Path(str(item.filename)).suffix.lower() in IMAGE_SUFFIXES
        ),
        total_bytes=int(record.total_bytes),
        ready_at=completed_at,
    )
    session.add(dataset)
    session.flush()
    return dataset


def finalize_from_intent(
    session: Session,
    record_id: int,
) -> UploadFinalizeResponse:
    """Publish an idempotent manifest before committing the catalog transition."""

    record = cast(
        DatasetUploadSession,
        session.query(DatasetUploadSession)
        .filter(DatasetUploadSession.id == record_id)
        .with_for_update()
        .one(),
    )
    if record.status == "completed":
        ensure_catalog_record(session, record)
        session.commit()
        return finalize_response(record)
    if record.status != "finalizing":
        raise RuntimeError("Dataset upload has no durable finalization intent")
    completed_at = cast(datetime | None, record.completed_at)
    if completed_at is None:
        raise RuntimeError("Finalizing dataset upload has no completion timestamp")
    prefix = record_dataset_prefix(record)
    manifest_key = f"{prefix}/dataset-manifest.json"
    manifest = {
        "schema_version": 1,
        "upload_session_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "created_by": str(record.created_by),
        "organization_id": str(record.organization_id),
        "completed_at": aware(completed_at).isoformat(),
        "files": [
            {
                "name": str(item.filename),
                "s3_key": str(item.s3_key),
                "size": int(item.size_bytes),
                "etag": str(item.etag or ""),
            }
            for item in sorted(record.files, key=lambda value: value.filename)
        ],
    }
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    try:
        storage.put_object(manifest_key, payload)
        if storage.get_object_size(manifest_key) != len(payload):
            raise RuntimeError("Dataset manifest verification failed")
    except Exception as error:
        record.last_error = str(error)[:4000]
        session.commit()
        raise
    record.status = "completed"
    record.last_error = None
    ensure_catalog_record(session, record)
    session.commit()
    return finalize_response(record)


def abort_record(record: DatasetUploadSession) -> None:
    errors: list[str] = []
    for item in record.files:
        try:
            if item.status == "initializing":
                for upload_id in storage.list_multipart_uploads(str(item.s3_key)):
                    try:
                        storage.abort_multipart_upload(str(item.s3_key), upload_id)
                    except Exception as error:
                        if storage_error_code(error) != "NoSuchUpload":
                            raise
            elif item.status == "uploading" and item.multipart_upload_id:
                storage.abort_multipart_upload(
                    str(item.s3_key),
                    str(item.multipart_upload_id),
                )
            elif item.status == "completing":
                raise RuntimeError("file completion is pending reconciliation")
            elif item.status == "completed":
                storage.delete_object(str(item.s3_key))
            item.status = "aborted"
            item.last_error = None
        except Exception as error:
            if item.status == "uploading" and storage_error_code(error) == "NoSuchUpload":
                item.status = "aborted"
                continue
            errors.append(f"{item.filename}: {error}")
    record.status = "failed" if errors else "aborted"
    record.last_error = "; ".join(errors)[:4000] if errors else None
    if errors:
        raise RuntimeError("; ".join(errors))
