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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from shared import storage
from shared.database import (
    DatasetUploadFile,
    DatasetUploadSession,
    get_session,
)

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
    files = sorted(record.files, key=lambda item: item.filename)
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


def create_upload_session(
    session: Session,
    request: UploadSessionRequest,
    principal: Principal,
) -> UploadSessionResponse:
    safe_name, total_size = _validate_request(request)
    existing = cast(
        DatasetUploadSession | None,
        session.query(DatasetUploadSession)
        .filter(
            DatasetUploadSession.dataset_name == safe_name,
            DatasetUploadSession.status.in_(("uploading", "failed")),
        )
        .first(),
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Dataset upload already in progress")
    if storage.list_objects(f"datasets/{safe_name}/"):
        raise HTTPException(status_code=409, detail="Dataset already exists")

    part_size = configured_part_size(max(item.size for item in request.files))
    record = DatasetUploadSession(
        dataset_name=safe_name,
        status="uploading",
        total_bytes=total_size,
        file_count=len(request.files),
        part_size=part_size,
        created_by=principal.subject,
        expires_at=datetime.now(UTC) + _session_lifetime(),
    )
    session.add(record)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        collision = (
            session.query(DatasetUploadSession)
            .filter(
                DatasetUploadSession.dataset_name == safe_name,
                DatasetUploadSession.status.in_(("uploading", "failed")),
            )
            .first()
        )
        if collision is not None:
            raise HTTPException(
                status_code=409,
                detail="Dataset upload already in progress",
            ) from None
        raise

    created: list[tuple[str, str]] = []
    try:
        for item in request.files:
            s3_key = f"datasets/{safe_name}/{item.name}"
            upload_id = storage.create_multipart_upload(
                s3_key,
                content_type=item.content_type or "application/octet-stream",
                metadata={
                    "droneai-upload-session": str(record.session_id),
                    "expected-size": str(item.size),
                },
            )
            created.append((s3_key, upload_id))
            session.add(
                DatasetUploadFile(
                    upload_session_id=record.id,
                    filename=item.name,
                    s3_key=s3_key,
                    size_bytes=item.size,
                    content_type=item.content_type or "application/octet-stream",
                    multipart_upload_id=upload_id,
                    status="uploading",
                )
            )
        session.flush()
    except Exception as error:
        for s3_key, upload_id in created:
            try:
                storage.abort_multipart_upload(s3_key, upload_id)
            except Exception:
                logger.exception("Failed to abort partial upload initialization")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Object storage upload initialization failed: {error}",
        ) from error
    session.refresh(record)
    return _serialize_session(record)


def _owned_session(
    session: Session,
    session_id: str,
    principal: Principal,
    *,
    lock: bool = False,
) -> DatasetUploadSession:
    query = session.query(DatasetUploadSession).filter(
        DatasetUploadSession.session_id == session_id
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
        return {
            "file_id": str(file_record.file_id),
            "name": str(file_record.filename),
            "s3_key": str(file_record.s3_key),
            "size": int(file_record.size_bytes),
            "etag": str(file_record.etag or ""),
            "status": "completed",
        }
    if file_record.status != "uploading":
        raise HTTPException(status_code=409, detail="Upload file is not active")

    expected = math.ceil(int(file_record.size_bytes) / int(record.part_size))
    ordered = sorted(request.parts, key=lambda part: part.part_number)
    if [part.part_number for part in ordered] != list(range(1, expected + 1)):
        raise HTTPException(
            status_code=400,
            detail="Completed parts must contain every part exactly once",
        )
    result = storage.complete_multipart_upload(
        str(file_record.s3_key),
        str(file_record.multipart_upload_id),
        [
            {"PartNumber": part.part_number, "ETag": part.etag}
            for part in ordered
        ],
    )
    if int(result["size"]) != int(file_record.size_bytes):
        storage.delete_object(str(file_record.s3_key))
        file_record.status = "failed"
        record.status = "failed"
        session.flush()
        session.commit()
        raise HTTPException(
            status_code=422,
            detail="Completed object size does not match the declared file size",
        )
    file_record.status = "completed"
    file_record.completed_parts = [
        {"part_number": part.part_number, "etag": part.etag}
        for part in ordered
    ]
    file_record.etag = str(result["etag"])
    session.flush()
    return {
        "file_id": str(file_record.file_id),
        "name": str(file_record.filename),
        "s3_key": str(file_record.s3_key),
        "size": int(file_record.size_bytes),
        "etag": str(file_record.etag),
        "status": "completed",
    }


def finalize_upload_session(
    session: Session,
    session_id: str,
    principal: Principal,
) -> UploadFinalizeResponse:
    record = _owned_session(session, session_id, principal, lock=True)
    if record.status == "completed":
        return _finalize_response(record)
    _require_uploading(record)
    if any(item.status != "completed" for item in record.files):
        raise HTTPException(status_code=409, detail="Upload files are incomplete")

    completed_at = datetime.now(UTC)
    manifest_key = f"datasets/{record.dataset_name}/dataset-manifest.json"
    manifest = {
        "schema_version": 1,
        "upload_session_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "created_by": str(record.created_by),
        "completed_at": completed_at.isoformat(),
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
    storage.put_object(manifest_key, payload)
    if storage.get_object_size(manifest_key) != len(payload):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Dataset manifest verification failed",
        )
    record.status = "completed"
    record.completed_at = completed_at
    session.flush()
    return _finalize_response(record)


def _finalize_response(record: DatasetUploadSession) -> UploadFinalizeResponse:
    return {
        "upload_id": str(record.session_id),
        "dataset": str(record.dataset_name),
        "total": int(record.file_count),
        "completed": int(record.file_count),
        "failed": 0,
        "status": "done",
        "manifest_s3_key": f"datasets/{record.dataset_name}/dataset-manifest.json",
    }


def _abort_record(record: DatasetUploadSession) -> None:
    errors: list[str] = []
    for item in record.files:
        try:
            if item.status == "uploading":
                storage.abort_multipart_upload(
                    str(item.s3_key),
                    str(item.multipart_upload_id),
                )
            elif item.status == "completed":
                storage.delete_object(str(item.s3_key))
            item.status = "aborted"
        except Exception as error:
            if item.status == "uploading" and _storage_error_code(error) == "NoSuchUpload":
                item.status = "aborted"
                continue
            errors.append(f"{item.filename}: {error}")
    record.status = "failed" if errors else "aborted"
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
    try:
        _abort_record(record)
    except RuntimeError as error:
        session.flush()
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Upload cleanup incomplete: {error}",
        ) from error
    session.flush()
    return {"session_id": str(record.session_id), "status": "aborted"}


def _expired_upload_query(
    session: Session,
    now: datetime,
) -> Query[DatasetUploadSession]:
    return (
        session.query(DatasetUploadSession)
        .filter(
            DatasetUploadSession.status.in_(("uploading", "failed")),
            DatasetUploadSession.expires_at <= now,
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
            cleanup_expired_uploads()
        except Exception:
            logger.exception("Dataset upload cleanup pass failed")
        stop_event.wait(interval)
