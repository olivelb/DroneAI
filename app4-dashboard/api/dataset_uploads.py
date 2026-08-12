"""Transactional command facade for durable direct-to-S3 dataset uploads."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from threading import Event
from typing import cast

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from shared import storage
from shared.database import Dataset, DatasetUploadFile, DatasetUploadSession, get_session
from shared.organization_saas import (
    StorageQuotaExceeded,
    check_storage_reservation,
    record_storage_release,
    record_storage_reservation,
)
from shared.tenancy import dataset_prefix

from . import dataset_upload_contracts as contracts
from . import dataset_upload_recovery as recovery
from . import dataset_upload_storage as storage_transitions
from .dataset_upload_contracts import (
    ACTIVE_UPLOAD_STATUSES,
    DATASET_SUFFIXES as DATASET_SUFFIXES,
    IMAGE_SUFFIXES as IMAGE_SUFFIXES,
    MAX_MULTIPART_PARTS as MAX_MULTIPART_PARTS,
    MAX_PART_BYTES as MAX_PART_BYTES,
    MIN_PART_BYTES as MIN_PART_BYTES,
    CompleteUploadFileRequest as CompleteUploadFileRequest,
    CompletedPartRequest as CompletedPartRequest,
    UploadFileCompleteResponse as UploadFileCompleteResponse,
    UploadFileDescriptor as UploadFileDescriptor,
    UploadFinalizeResponse as UploadFinalizeResponse,
    UploadPartUrlResponse as UploadPartUrlResponse,
    UploadSessionFileRequest as UploadSessionFileRequest,
    UploadSessionRequest as UploadSessionRequest,
    UploadSessionResponse as UploadSessionResponse,
)
from .security import Principal


def sanitize_dataset_name(value: str) -> str:
    return contracts.sanitize_dataset_name(value)


def configured_part_size(max_file_size: int) -> int:
    return contracts.configured_part_size(max_file_size)


def _session_lifetime() -> timedelta:
    return contracts.session_lifetime()


def _part_url_lifetime() -> int:
    return contracts.part_url_lifetime()


def _aware(value: datetime) -> datetime:
    return contracts.aware(value)


def _validate_request(request: UploadSessionRequest) -> tuple[str, int]:
    return contracts.validate_request(request)


def _serialize_session(record: DatasetUploadSession) -> UploadSessionResponse:
    return contracts.serialize_session(record)


def _matching_upload_request(
    record: DatasetUploadSession,
    request: UploadSessionRequest,
) -> bool:
    return contracts.matching_upload_request(record, request)


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


def _initialize_pending_files(session: Session, record_id: int) -> None:
    storage_transitions.initialize_pending_files(session, record_id)


def create_upload_session(
    session: Session,
    request: UploadSessionRequest,
    principal: Principal,
) -> UploadSessionResponse:
    safe_name, total_size = _validate_request(request)
    prefix = dataset_prefix(principal.organization_id, safe_name)
    existing = _active_upload(session, safe_name, principal.organization_id)
    if existing is not None:
        if existing.created_by != principal.subject or not _matching_upload_request(
            existing,
            request,
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
        collision = _active_upload(session, safe_name, principal.organization_id)
        if collision is None:
            raise
        if collision.created_by != principal.subject or not _matching_upload_request(
            collision,
            request,
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
    return contracts.completed_file_response(file_record)


def _normalized_parts(
    request: CompleteUploadFileRequest,
    expected: int,
) -> list[dict[str, int | str]]:
    return contracts.normalized_parts(request, expected)


def _complete_file_from_intent(
    session: Session,
    record_id: int,
    file_record_id: int,
) -> UploadFileCompleteResponse | None:
    return storage_transitions.complete_file_from_intent(
        session,
        record_id,
        file_record_id,
    )


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


def _ensure_catalog_record(
    session: Session,
    record: DatasetUploadSession,
) -> Dataset:
    return storage_transitions.ensure_catalog_record(session, record)


def _finalize_response(record: DatasetUploadSession) -> UploadFinalizeResponse:
    return contracts.finalize_response(record)


def _finalize_from_intent(
    session: Session,
    record_id: int,
) -> UploadFinalizeResponse:
    return storage_transitions.finalize_from_intent(session, record_id)


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


def _abort_record(record: DatasetUploadSession) -> None:
    storage_transitions.abort_record(record)


def abort_upload_session(
    session: Session,
    session_id: str,
    principal: Principal,
) -> dict[str, str]:
    record = _owned_session(session, session_id, principal, lock=True)
    if record.status == "aborted":
        return {"session_id": str(record.session_id), "status": str(record.status)}
    if record.status == "completed":
        raise HTTPException(
            status_code=409,
            detail="Completed upload cannot be aborted",
        )
    has_uncertain_completion = any(item.status == "completing" for item in record.files)
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
    return recovery.pending_upload_query(session)


def reconcile_pending_uploads() -> int:
    return recovery.reconcile_pending_uploads(session_factory=get_session)


def _expired_upload_query(
    session: Session,
    now: datetime,
) -> Query[DatasetUploadSession]:
    return recovery.expired_upload_query(session, now)


def cleanup_expired_uploads() -> int:
    return recovery.cleanup_expired_uploads(session_factory=get_session)


def run_upload_cleanup(stop_event: Event) -> None:
    recovery.run_upload_cleanup(stop_event, session_factory=get_session)
