"""Durable direct-to-S3 multipart dataset upload orchestration."""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import TypedDict, cast

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from shared import storage
from shared.database import (
    Dataset,
    DatasetUploadFile,
    DatasetUploadSession,
    get_session,
)
from shared.organization_saas import (
    StorageQuotaExceeded,
    check_storage_reservation,
    record_storage_release,
    record_storage_reservation,
)
from shared.tenancy import dataset_prefix

from .security import Principal, upload_limits

logger = logging.getLogger(__name__)

DATASET_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tif",
    ".tiff",
    ".mrk",
    ".nav",
    ".obs",
    ".bin",
    ".rtk",
    ".txt",
    ".csv",
}
MIN_PART_BYTES = 5 * 1024 * 1024
MAX_PART_BYTES = 512 * 1024 * 1024
MAX_MULTIPART_PARTS = 10_000
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
ACTIVE_UPLOAD_STATUSES = ("initializing", "uploading", "finalizing", "failed")


def _storage_error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if isinstance(response, dict):
        details = response.get("Error")
        if isinstance(details, dict):
            code = details.get("Code")
            if isinstance(code, str):
                return code
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


class UploadSessionFileRequest(BaseModel):  # type: ignore[misc]
    name: str = Field(min_length=1, max_length=512)
    size: int = Field(gt=0)
    content_type: str = Field(default="application/octet-stream", max_length=256)

    @field_validator("name")  # type: ignore[untyped-decorator]
    @classmethod
    def filename_only(cls, value: str) -> str:
        if Path(value).name != value or value in {".", ".."}:
            raise ValueError("name must be a plain filename")
        return value

    @field_validator("content_type")  # type: ignore[untyped-decorator]
    @classmethod
    def safe_content_type(cls, value: str) -> str:
        if any(character in value for character in "\r\n\x00"):
            raise ValueError("content_type contains control characters")
        return value


class UploadSessionRequest(BaseModel):  # type: ignore[misc]
    dataset_name: str = Field(min_length=1, max_length=256)
    files: list[UploadSessionFileRequest] = Field(min_length=1)


class CompletedPartRequest(BaseModel):  # type: ignore[misc]
    part_number: int = Field(ge=1, le=MAX_MULTIPART_PARTS)
    etag: str = Field(min_length=1, max_length=256)

    @field_validator("etag")  # type: ignore[untyped-decorator]
    @classmethod
    def safe_etag(cls, value: str) -> str:
        if any(character in value for character in "\r\n\x00"):
            raise ValueError("etag contains control characters")
        return value


class CompleteUploadFileRequest(BaseModel):  # type: ignore[misc]
    parts: list[CompletedPartRequest] = Field(min_length=1, max_length=MAX_MULTIPART_PARTS)


class UploadFileDescriptor(TypedDict):
    file_id: str
    name: str
    size: int
    s3_key: str
    total_parts: int
    status: str


class UploadSessionResponse(TypedDict):
    session_id: str
    dataset: str
    status: str
    total: int
    total_bytes: int
    part_size: int
    expires_at: str
    files: list[UploadFileDescriptor]


class UploadPartUrlResponse(TypedDict):
    method: str
    url: str
    expires_in: int
    part_number: int


class UploadFileCompleteResponse(TypedDict):
    file_id: str
    name: str
    s3_key: str
    size: int
    etag: str
    status: str


class UploadFinalizeResponse(TypedDict):
    upload_id: str
    dataset: str
    total: int
    completed: int
    failed: int
    status: str
    manifest_s3_key: str


def sanitize_dataset_name(value: str) -> str:
    return "".join(
        character if character.isascii() and (character.isalnum() or character in "_-") else "_"
        for character in value.strip()
    )


def configured_part_size(max_file_size: int) -> int:
    raw = os.getenv("DRONEAI_UPLOAD_PART_BYTES", str(16 * 1024 * 1024))
    try:
        requested = int(raw)
    except ValueError as error:
        raise RuntimeError("DRONEAI_UPLOAD_PART_BYTES must be an integer") from error
    if not MIN_PART_BYTES <= requested <= MAX_PART_BYTES:
        raise RuntimeError(
            "DRONEAI_UPLOAD_PART_BYTES must be between 5 MiB and 512 MiB"
        )
    required = math.ceil(max_file_size / MAX_MULTIPART_PARTS)
    mebibyte = 1024 * 1024
    calculated = max(requested, math.ceil(required / mebibyte) * mebibyte)
    if calculated > MAX_PART_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="File is too large for the configured multipart limits",
        )
    return calculated


def _session_lifetime() -> timedelta:
    try:
        seconds = int(os.getenv("DRONEAI_UPLOAD_SESSION_SECONDS", "86400"))
    except ValueError as error:
        raise RuntimeError("DRONEAI_UPLOAD_SESSION_SECONDS must be an integer") from error
    if not 900 <= seconds <= 7 * 86400:
        raise RuntimeError(
            "DRONEAI_UPLOAD_SESSION_SECONDS must be between 900 and 604800"
        )
    return timedelta(seconds=seconds)


def _part_url_lifetime() -> int:
    try:
        seconds = int(os.getenv("DRONEAI_UPLOAD_PART_URL_SECONDS", "900"))
    except ValueError as error:
        raise RuntimeError("DRONEAI_UPLOAD_PART_URL_SECONDS must be an integer") from error
    if not 60 <= seconds <= 3600:
        raise RuntimeError(
            "DRONEAI_UPLOAD_PART_URL_SECONDS must be between 60 and 3600"
        )
    return seconds


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _validate_request(request: UploadSessionRequest) -> tuple[str, int]:
    limits = upload_limits()
    if len(request.files) > limits["max_files"]:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Upload contains too many files",
        )
    safe_name = sanitize_dataset_name(request.dataset_name)
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid dataset name")
    total_size = 0
    filenames: set[str] = set()
    for item in request.files:
        if Path(item.name).suffix.lower() not in DATASET_SUFFIXES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported dataset file: {item.name}",
            )
        if item.name in filenames:
            raise HTTPException(status_code=409, detail=f"Duplicate filename: {item.name}")
        filenames.add(item.name)
        if item.size > limits["max_file_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Dataset file exceeds quota: {item.name}",
            )
        total_size += item.size
        if total_size > limits["max_batch_bytes"]:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Upload batch exceeds quota",
            )
    return safe_name, total_size


def _serialize_session(record: DatasetUploadSession) -> UploadSessionResponse:
    part_size = int(record.part_size)
    files = sorted(
        (item for item in record.files if item.status == "uploading"),
        key=lambda item: item.filename,
    )
    return {
        "session_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "status": str(record.status),
        "total": int(record.file_count),
        "total_bytes": int(record.total_bytes),
        "part_size": part_size,
        "expires_at": _aware(cast(datetime, record.expires_at)).isoformat(),
        "files": [
            {
                "file_id": str(item.file_id),
                "name": str(item.filename),
                "size": int(item.size_bytes),
                "s3_key": str(item.s3_key),
                "total_parts": math.ceil(int(item.size_bytes) / part_size),
                "status": str(item.status),
            }
            for item in files
        ],
    }


def _matching_upload_request(
    record: DatasetUploadSession,
    request: UploadSessionRequest,
) -> bool:
    requested = sorted(
        (
            item.name,
            int(item.size),
            item.content_type or "application/octet-stream",
        )
        for item in request.files
    )
    persisted = sorted(
        (
            str(item.filename),
            int(item.size_bytes),
            str(item.content_type),
        )
        for item in record.files
    )
    return requested == persisted


def _active_upload(
    session: Session,
    dataset_name: str,
    organization_id: str,
) -> DatasetUploadSession | None:
    return cast(
        DatasetUploadSession | None,
        session.query(DatasetUploadSession)
        .filter(
            DatasetUploadSession.dataset_name == dataset_name,
            DatasetUploadSession.organization_id == organization_id,
            DatasetUploadSession.status.in_(ACTIVE_UPLOAD_STATUSES),
        )
        .first(),
    )


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


def _initialize_pending_files(session: Session, record_id: int) -> None:
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
                    if _storage_error_code(error) != "NoSuchUpload":
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
                    storage.abort_multipart_upload(
                        key,
                        created_upload_id,
                    )
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


def create_upload_session(
    session: Session,
    request: UploadSessionRequest,
    principal: Principal,
) -> UploadSessionResponse:
    safe_name, total_size = _validate_request(request)
    prefix = dataset_prefix(principal.organization_id, safe_name)
    existing = _active_upload(
        session,
        safe_name,
        principal.organization_id,
    )
    if existing is not None:
        if (
            existing.created_by != principal.subject
            or not _matching_upload_request(existing, request)
        ):
            raise HTTPException(
                status_code=409,
                detail="Dataset upload already in progress",
            )
        if existing.status == "initializing":
            try:
                _initialize_pending_files(session, int(existing.id))
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Object storage upload initialization failed: {error}",
                ) from error
            existing = cast(
                DatasetUploadSession,
                session.query(DatasetUploadSession)
                .filter(DatasetUploadSession.id == existing.id)
                .one(),
            )
        return _serialize_session(existing)
    if storage.list_objects(f"{prefix}/"):
        raise HTTPException(status_code=409, detail="Dataset already exists")

    try:
        check_storage_reservation(
            session,
            organization_id=principal.organization_id,
            requested_bytes=total_size,
        )
    except StorageQuotaExceeded as error:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "message": "Organization storage quota exceeded",
                "current_bytes": error.current_bytes,
                "requested_bytes": error.requested_bytes,
                "limit_bytes": error.limit_bytes,
            },
        ) from error

    part_size = configured_part_size(max(item.size for item in request.files))
    record = DatasetUploadSession(
        dataset_name=safe_name,
        organization_id=principal.organization_id,
        status="initializing",
        total_bytes=total_size,
        file_count=len(request.files),
        part_size=part_size,
        created_by=principal.subject,
        expires_at=datetime.now(UTC) + _session_lifetime(),
    )
    session.add(record)
    for item in request.files:
        session.add(
            DatasetUploadFile(
                upload_session=record,
                filename=item.name,
                s3_key=f"{prefix}/{item.name}",
                size_bytes=item.size,
                content_type=item.content_type or "application/octet-stream",
                multipart_upload_id=None,
                status="initializing",
            )
        )
    try:
        session.flush()
        record_storage_reservation(
            session,
            organization_id=principal.organization_id,
            upload_session_id=str(record.session_id),
            requested_bytes=total_size,
            actor_subject=principal.subject,
        )
        session.commit()
    except IntegrityError:
        session.rollback()
        collision = _active_upload(
            session,
            safe_name,
            principal.organization_id,
        )
        if collision is None:
            raise
        if (
            collision.created_by != principal.subject
            or not _matching_upload_request(collision, request)
        ):
            raise HTTPException(
                status_code=409,
                detail="Dataset upload already in progress",
            ) from None
        record = collision
    try:
        _initialize_pending_files(session, int(record.id))
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Object storage upload initialization failed: {error}",
        ) from error
    record = cast(
        DatasetUploadSession,
        session.query(DatasetUploadSession)
        .filter(DatasetUploadSession.id == record.id)
        .one(),
    )
    return _serialize_session(record)


def _owned_session(
    session: Session,
    session_id: str,
    principal: Principal,
    *,
    lock: bool = False,
) -> DatasetUploadSession:
    query = session.query(DatasetUploadSession).filter(
        DatasetUploadSession.session_id == session_id,
        DatasetUploadSession.organization_id == principal.organization_id,
    )
    if lock:
        query = query.with_for_update()
    record = cast(DatasetUploadSession | None, query.first())
    if record is None or (
        principal.role != "admin" and record.created_by != principal.subject
    ):
        raise HTTPException(status_code=404, detail="Upload session not found")
    return record


def _session_file(
    record: DatasetUploadSession,
    file_id: str,
) -> DatasetUploadFile:
    result = next(
        (item for item in record.files if str(item.file_id) == file_id),
        None,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Upload file not found")
    return cast(DatasetUploadFile, result)


def _require_uploading(record: DatasetUploadSession) -> None:
    if record.status != "uploading":
        raise HTTPException(status_code=409, detail="Upload session is not active")
    if _aware(cast(datetime, record.expires_at)) <= datetime.now(UTC):
        raise HTTPException(status_code=410, detail="Upload session has expired")


def _completed_file_response(
    file_record: DatasetUploadFile,
) -> UploadFileCompleteResponse:
    return {
        "file_id": str(file_record.file_id),
        "name": str(file_record.filename),
        "s3_key": str(file_record.s3_key),
        "size": int(file_record.size_bytes),
        "etag": str(file_record.etag or ""),
        "status": "completed",
    }


def _normalized_parts(
    request: CompleteUploadFileRequest,
    expected: int,
) -> list[dict[str, int | str]]:
    ordered = sorted(request.parts, key=lambda part: part.part_number)
    if [part.part_number for part in ordered] != list(range(1, expected + 1)):
        raise HTTPException(
            status_code=400,
            detail="Completed parts must contain every part exactly once",
        )
    return [
        {"part_number": part.part_number, "etag": part.etag}
        for part in ordered
    ]


def _stored_s3_parts(file_record: DatasetUploadFile) -> list[dict[str, int | str]]:
    raw_parts = file_record.completed_parts
    if not isinstance(raw_parts, list) or not raw_parts:
        raise RuntimeError("Completing upload has no durable multipart part list")
    return [
        {
            "PartNumber": int(
                cast(dict[str, int | str], part)["part_number"]
            ),
            "ETag": str(cast(dict[str, int | str], part)["etag"]),
        }
        for part in raw_parts
    ]


def _object_identity(
    record: DatasetUploadSession,
    file_record: DatasetUploadFile,
    info: dict[str, int | str | dict[str, str]],
) -> tuple[bool, bool]:
    metadata = cast(dict[str, str], info["metadata"])
    session_matches = metadata.get("droneai-upload-session") == str(
        record.session_id
    )
    persisted_file_id = metadata.get("droneai-upload-file")
    file_matches = persisted_file_id in {None, "", str(file_record.file_id)}
    expected_size_matches = metadata.get("expected-size") == str(
        file_record.size_bytes
    )
    owned = session_matches and file_matches
    valid = (
        owned
        and expected_size_matches
        and int(cast(int | str, info["size"])) == int(file_record.size_bytes)
    )
    return owned, valid


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


def _complete_file_from_intent(
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
        return _completed_file_response(file_record)
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
        try:
            storage.complete_multipart_upload(
                str(file_record.s3_key),
                upload_id,
                _stored_s3_parts(file_record),
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
            if info is None and _storage_error_code(error) == "NoSuchUpload":
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

    owned, valid = _object_identity(record, file_record, info)
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
    return _completed_file_response(file_record)


def create_part_url(
    session: Session,
    session_id: str,
    file_id: str,
    part_number: int,
    principal: Principal,
) -> UploadPartUrlResponse:
    record = _owned_session(session, session_id, principal)
    _require_uploading(record)
    file_record = _session_file(record, file_id)
    if file_record.status != "uploading":
        raise HTTPException(status_code=409, detail="Upload file is not active")
    total_parts = math.ceil(int(file_record.size_bytes) / int(record.part_size))
    if not 1 <= part_number <= total_parts:
        raise HTTPException(status_code=400, detail="Invalid upload part number")
    expires = _part_url_lifetime()
    return {
        "method": "PUT",
        "url": storage.get_presigned_upload_part_url(
            str(file_record.s3_key),
            str(file_record.multipart_upload_id),
            part_number,
            expires=expires,
        ),
        "expires_in": expires,
        "part_number": part_number,
    }


def complete_upload_file(
    session: Session,
    session_id: str,
    file_id: str,
    request: CompleteUploadFileRequest,
    principal: Principal,
) -> UploadFileCompleteResponse:
    record = _owned_session(session, session_id, principal, lock=True)
    _require_uploading(record)
    file_record = _session_file(record, file_id)
    if file_record.status == "completed":
        return _completed_file_response(file_record)
    if file_record.status not in {"uploading", "completing"}:
        raise HTTPException(status_code=409, detail="Upload file is not active")

    expected = math.ceil(int(file_record.size_bytes) / int(record.part_size))
    normalized = _normalized_parts(request, expected)
    if file_record.status == "uploading":
        file_record.status = "completing"
        file_record.completed_parts = normalized
        file_record.last_error = None
        session.commit()
    elif file_record.completed_parts != normalized:
        raise HTTPException(
            status_code=409,
            detail="Multipart completion is already in progress with different parts",
        )
    try:
        result = _complete_file_from_intent(
            session,
            int(record.id),
            int(file_record.id),
        )
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Object storage completion is pending recovery: {error}",
        ) from error
    if result is None:
        raise HTTPException(status_code=409, detail="Upload file is not active")
    return result


def finalize_upload_session(
    session: Session,
    session_id: str,
    principal: Principal,
) -> UploadFinalizeResponse:
    record = _owned_session(session, session_id, principal, lock=True)
    if record.status == "completed":
        _ensure_catalog_record(session, record)
        session.commit()
        return _finalize_response(record)
    if record.status == "uploading":
        _require_uploading(record)
        if any(item.status != "completed" for item in record.files):
            raise HTTPException(status_code=409, detail="Upload files are incomplete")
        record.status = "finalizing"
        record.completed_at = datetime.now(UTC)
        record.last_error = None
        session.commit()
    elif record.status != "finalizing":
        raise HTTPException(status_code=409, detail="Upload session is not active")
    try:
        return _finalize_from_intent(session, int(record.id))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Dataset finalization is pending recovery: {error}",
        ) from error


def _finalize_from_intent(
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
        _ensure_catalog_record(session, record)
        session.commit()
        return _finalize_response(record)
    if record.status != "finalizing":
        raise RuntimeError("Dataset upload has no durable finalization intent")
    completed_at = cast(datetime | None, record.completed_at)
    if completed_at is None:
        raise RuntimeError("Finalizing dataset upload has no completion timestamp")
    prefix = _record_dataset_prefix(record)
    manifest_key = f"{prefix}/dataset-manifest.json"
    manifest = {
        "schema_version": 1,
        "upload_session_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "created_by": str(record.created_by),
        "organization_id": str(record.organization_id),
        "completed_at": _aware(completed_at).isoformat(),
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
    _ensure_catalog_record(session, record)
    session.commit()
    return _finalize_response(record)


def _record_dataset_prefix(record: DatasetUploadSession) -> str:
    """Recover the durable prefix from file intent across v1/v2 layouts."""

    first_file = next(iter(record.files), None)
    if first_file is not None:
        return str(first_file.s3_key).rsplit("/", 1)[0]
    return cast(
        str,
        dataset_prefix(
            str(record.organization_id),
            str(record.dataset_name),
        ),
    )


def _ensure_catalog_record(
    session: Session,
    record: DatasetUploadSession,
) -> Dataset:
    existing = cast(
        Dataset | None,
        session.query(Dataset)
        .filter(Dataset.upload_session_id == record.id)
        .first(),
    )
    if existing is not None:
        return existing
    completed_at = cast(datetime | None, record.completed_at)
    if completed_at is None:
        raise RuntimeError("Completed dataset upload has no completion timestamp")
    prefix = _record_dataset_prefix(record)
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


def _finalize_response(record: DatasetUploadSession) -> UploadFinalizeResponse:
    return {
        "upload_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "total": int(record.file_count),
        "completed": int(record.file_count),
        "failed": 0,
        "status": "done",
        "manifest_s3_key": f"{_record_dataset_prefix(record)}/dataset-manifest.json",
    }


def _abort_record(record: DatasetUploadSession) -> None:
    errors: list[str] = []
    for item in record.files:
        try:
            if item.status == "initializing":
                for upload_id in storage.list_multipart_uploads(str(item.s3_key)):
                    try:
                        storage.abort_multipart_upload(str(item.s3_key), upload_id)
                    except Exception as error:
                        if _storage_error_code(error) != "NoSuchUpload":
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
            if item.status == "uploading" and _storage_error_code(error) == "NoSuchUpload":
                item.status = "aborted"
                continue
            errors.append(f"{item.filename}: {error}")
    record.status = "failed" if errors else "aborted"
    record.last_error = "; ".join(errors)[:4000] if errors else None
    if errors:
        raise RuntimeError("; ".join(errors))


def abort_upload_session(
    session: Session,
    session_id: str,
    principal: Principal,
) -> dict[str, str]:
    record = _owned_session(session, session_id, principal, lock=True)
    if record.status == "aborted":
        return {"session_id": str(record.session_id), "status": str(record.status)}
    if record.status == "completed":
        raise HTTPException(status_code=409, detail="Completed upload cannot be aborted")
    has_uncertain_completion = any(
        item.status == "completing" for item in record.files
    )
    has_resumable_progress = record.status == "uploading" and any(
        item.status == "completed" for item in record.files
    )
    if (
        record.status == "finalizing"
        or has_uncertain_completion
        or has_resumable_progress
    ):
        raise HTTPException(
            status_code=409,
            detail="Upload has recoverable progress and cannot be aborted",
        )
    try:
        _abort_record(record)
    except RuntimeError as error:
        session.flush()
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upload cleanup incomplete: {error}",
        ) from error
    record_storage_release(
        session,
        organization_id=str(record.organization_id),
        resource_type="dataset_upload",
        resource_id=str(record.session_id),
        released_bytes=int(record.total_bytes),
        actor_subject=principal.subject,
        idempotency_key=f"storage-released:upload:{record.session_id}",
    )
    session.commit()
    return {"session_id": str(record.session_id), "status": "aborted"}


def _pending_upload_query(session: Session) -> Query[DatasetUploadSession]:
    return (
        session.query(DatasetUploadSession)
        .filter(
            or_(
                DatasetUploadSession.status.in_(("initializing", "finalizing")),
                DatasetUploadSession.files.any(
                    DatasetUploadFile.status == "completing"
                ),
            )
        )
        .order_by(DatasetUploadSession.updated_at, DatasetUploadSession.id)
        .with_for_update(skip_locked=True)
        .limit(100)
    )


def reconcile_pending_uploads() -> int:
    """Retry crash-safe storage transitions claimed with ``SKIP LOCKED``."""

    reconciled = 0
    with get_session() as session:
        records = cast(list[DatasetUploadSession], _pending_upload_query(session).all())
        record_ids = [int(record.id) for record in records]
        for record_id in record_ids:
            try:
                record = cast(
                    DatasetUploadSession,
                    session.query(DatasetUploadSession)
                    .filter(DatasetUploadSession.id == record_id)
                    .one(),
                )
                if record.status == "initializing":
                    _initialize_pending_files(session, record_id)
                record = cast(
                    DatasetUploadSession,
                    session.query(DatasetUploadSession)
                    .filter(DatasetUploadSession.id == record_id)
                    .one(),
                )
                completing_ids = [
                    int(item.id)
                    for item in record.files
                    if item.status == "completing"
                ]
                for file_record_id in completing_ids:
                    _complete_file_from_intent(
                        session,
                        record_id,
                        file_record_id,
                    )
                record = cast(
                    DatasetUploadSession,
                    session.query(DatasetUploadSession)
                    .filter(DatasetUploadSession.id == record_id)
                    .one(),
                )
                if record.status == "finalizing":
                    _finalize_from_intent(session, record_id)
                reconciled += 1
            except Exception:
                session.rollback()
                logger.exception(
                    "Dataset upload reconciliation failed for database id %s",
                    record_id,
                )
    return reconciled


def _expired_upload_query(
    session: Session,
    now: datetime,
) -> Query[DatasetUploadSession]:
    return (
        session.query(DatasetUploadSession)
        .filter(
            DatasetUploadSession.status.in_(("initializing", "uploading", "failed")),
            DatasetUploadSession.expires_at <= now,
            ~DatasetUploadSession.files.any(
                DatasetUploadFile.status == "completing"
            ),
        )
        .order_by(DatasetUploadSession.expires_at, DatasetUploadSession.id)
        .with_for_update(skip_locked=True)
        .limit(100)
    )


def cleanup_expired_uploads() -> int:
    now = datetime.now(UTC)
    cleaned = 0
    with get_session() as session:
        records = cast(
            list[DatasetUploadSession],
            _expired_upload_query(session, now).all(),
        )
        for record in records:
            try:
                _abort_record(record)
                record_storage_release(
                    session,
                    organization_id=str(record.organization_id),
                    resource_type="dataset_upload",
                    resource_id=str(record.session_id),
                    released_bytes=int(record.total_bytes),
                    actor_subject="system:upload-cleanup",
                    idempotency_key=(
                        f"storage-released:upload:{record.session_id}"
                    ),
                )
                cleaned += 1
            except RuntimeError:
                logger.exception(
                    "Expired dataset upload cleanup failed for %s",
                    record.session_id,
                )
    return cleaned


def run_upload_cleanup(stop_event: Event) -> None:
    try:
        interval = int(os.getenv("DRONEAI_UPLOAD_CLEANUP_SECONDS", "900"))
    except ValueError:
        logger.exception("Invalid DRONEAI_UPLOAD_CLEANUP_SECONDS; cleanup disabled")
        return
    if interval < 60:
        logger.error("DRONEAI_UPLOAD_CLEANUP_SECONDS must be at least 60")
        return
    while not stop_event.is_set():
        try:
            reconcile_pending_uploads()
            cleanup_expired_uploads()
        except Exception:
            logger.exception("Dataset upload cleanup pass failed")
        stop_event.wait(interval)
