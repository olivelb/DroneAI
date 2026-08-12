"""Background reconciliation and expiry cleanup for dataset uploads."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from threading import Event
from typing import cast

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from shared.database import DatasetUploadFile, DatasetUploadSession
from shared.observability import observe_control_loop, observe_reconciliation
from shared.organization_saas import record_storage_release

from . import dataset_upload_storage as storage_transitions

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


def pending_upload_query(session: Session) -> Query[DatasetUploadSession]:
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


def reconcile_pending_uploads(*, session_factory: SessionFactory) -> int:
    """Retry crash-safe storage transitions claimed with ``SKIP LOCKED``."""

    reconciled = 0
    with session_factory() as session:
        records = cast(list[DatasetUploadSession], pending_upload_query(session).all())
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
                    storage_transitions.initialize_pending_files(session, record_id)
                record = cast(
                    DatasetUploadSession,
                    session.query(DatasetUploadSession)
                    .filter(DatasetUploadSession.id == record_id)
                    .one(),
                )
                completing_ids = [
                    int(item.id) for item in record.files if item.status == "completing"
                ]
                for file_record_id in completing_ids:
                    storage_transitions.complete_file_from_intent(
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
                    storage_transitions.finalize_from_intent(session, record_id)
                reconciled += 1
            except Exception:
                session.rollback()
                logger.exception(
                    "Dataset upload reconciliation failed for database id %s",
                    record_id,
                )
    return reconciled


def expired_upload_query(
    session: Session,
    now: datetime,
) -> Query[DatasetUploadSession]:
    return (
        session.query(DatasetUploadSession)
        .filter(
            DatasetUploadSession.status.in_(("initializing", "uploading", "failed")),
            DatasetUploadSession.expires_at <= now,
            ~DatasetUploadSession.files.any(DatasetUploadFile.status == "completing"),
        )
        .order_by(DatasetUploadSession.expires_at, DatasetUploadSession.id)
        .with_for_update(skip_locked=True)
        .limit(100)
    )


def cleanup_expired_uploads(*, session_factory: SessionFactory) -> int:
    now = datetime.now(UTC)
    cleaned = 0
    with session_factory() as session:
        records = cast(
            list[DatasetUploadSession],
            expired_upload_query(session, now).all(),
        )
        for record in records:
            try:
                storage_transitions.abort_record(record)
                record_storage_release(
                    session,
                    organization_id=str(record.organization_id),
                    resource_type="dataset_upload",
                    resource_id=str(record.session_id),
                    released_bytes=int(record.total_bytes),
                    actor_subject="system:upload-cleanup",
                    idempotency_key=f"storage-released:upload:{record.session_id}",
                )
                cleaned += 1
            except RuntimeError:
                logger.exception(
                    "Expired dataset upload cleanup failed for %s",
                    record.session_id,
                )
    return cleaned


def run_upload_cleanup(
    stop_event: Event,
    *,
    session_factory: SessionFactory,
) -> None:
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
            reconciled = reconcile_pending_uploads(session_factory=session_factory)
            expired = cleanup_expired_uploads(session_factory=session_factory)
            observe_reconciliation("uploads", "reconciled", reconciled)
            observe_reconciliation("uploads", "expired", expired)
            observe_control_loop("uploads", succeeded=True)
        except Exception:
            observe_control_loop("uploads", succeeded=False)
            logger.exception("Dataset upload cleanup pass failed")
        stop_event.wait(interval)
