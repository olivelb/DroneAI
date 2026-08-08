"""Renewable durable inbox leases for long-running worker handlers."""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, cast
from collections.abc import Callable

from sqlalchemy.exc import IntegrityError

from shared.database import InboxEvent, get_session
from shared.event_contracts import validate_event
from shared.inbox_records import build_inbox_record
from shared.inbox_outbox import SessionScope, default_worker_id, utc_now
from shared.kafka_reliability import (
    MessageDeferredError,
    message_location,
)


WorkerHandler = Callable[[dict[str, Any]], None]


class WorkerInboxResult(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    IN_PROGRESS = "in_progress"


class InboxLeaseLostError(RuntimeError):
    """Raised when a worker no longer owns the durable inbox claim."""


def _lock_is_current(locked_at: datetime | None, cutoff: datetime) -> bool:
    if locked_at is None:
        return False
    comparable_cutoff = cutoff
    if locked_at.tzinfo is None and cutoff.tzinfo is not None:
        comparable_cutoff = cutoff.replace(tzinfo=None)
    return locked_at > comparable_cutoff


def _claim_inbox_work(
    session: Any,
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
    worker_id: str,
    now: datetime,
    lease_seconds: int,
) -> WorkerInboxResult:
    normalized = validate_event(event)
    record = (
        session.query(InboxEvent)
        .filter(
            InboxEvent.consumer_group == consumer_group,
            InboxEvent.event_id == normalized["event_id"],
        )
        .with_for_update()
        .first()
    )
    if record is not None and record.status == "completed":
        return WorkerInboxResult.DUPLICATE
    cutoff = now - timedelta(seconds=lease_seconds)
    if (
        record is not None
        and record.status == "processing"
        and _lock_is_current(record.locked_at, cutoff)
    ):
        return WorkerInboxResult.IN_PROGRESS

    if record is None:
        record = build_inbox_record(
            consumer_group=consumer_group,
            event=normalized,
            source=source,
        )
        session.add(record)
    else:
        record.attempts += 1
        record.source_topic = source.get("topic")
        record.source_partition = source.get("partition")
        record.source_offset = source.get("offset")
        record.payload = normalized
        record.status = "processing"
    record.last_error = None
    record.processed_at = None
    record.locked_at = now
    record.locked_by = worker_id
    session.flush()
    return WorkerInboxResult.PROCESSED


def claim_inbox_work(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
    worker_id: str,
    lease_seconds: int,
    now: datetime | None = None,
) -> WorkerInboxResult:
    """Claim long-running work in a short transaction."""

    current_time = now or utc_now()
    try:
        with session_scope() as session:
            return _claim_inbox_work(
                session,
                consumer_group=consumer_group,
                event=event,
                source=source,
                worker_id=worker_id,
                now=current_time,
                lease_seconds=lease_seconds,
            )
    except IntegrityError:
        # A concurrent insert won the unique consumer/event claim. Once that
        # transaction is visible, its active or completed receipt owns work.
        with session_scope() as session:
            record = (
                session.query(InboxEvent)
                .filter(
                    InboxEvent.consumer_group == consumer_group,
                    InboxEvent.event_id == event.get("event_id"),
                )
                .first()
            )
            if record is not None and record.status == "completed":
                return WorkerInboxResult.DUPLICATE
            if record is not None and record.status == "processing":
                return WorkerInboxResult.IN_PROGRESS
        raise


def _owned_claim(
    session: Any,
    *,
    consumer_group: str,
    event_id: str,
    worker_id: str,
) -> InboxEvent | None:
    return cast(
        InboxEvent | None,
        session.query(InboxEvent)
        .filter(
            InboxEvent.consumer_group == consumer_group,
            InboxEvent.event_id == event_id,
            InboxEvent.status == "processing",
            InboxEvent.locked_by == worker_id,
        )
        .with_for_update()
        .first(),
    )


def renew_inbox_claim(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    event_id: str,
    worker_id: str,
    now: datetime | None = None,
) -> bool:
    """Renew a claim only while the caller still owns it."""

    with session_scope() as session:
        record = _owned_claim(
            session,
            consumer_group=consumer_group,
            event_id=event_id,
            worker_id=worker_id,
        )
        if record is None:
            return False
        record.locked_at = now or utc_now()
        return True


def _finish_inbox_work(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    event_id: str,
    worker_id: str,
    error: BaseException | None,
) -> bool:
    with session_scope() as session:
        record = _owned_claim(
            session,
            consumer_group=consumer_group,
            event_id=event_id,
            worker_id=worker_id,
        )
        if record is None:
            return False
        record.locked_at = None
        record.locked_by = None
        if error is None:
            record.status = "completed"
            record.processed_at = utc_now()
            record.last_error = None
        else:
            record.status = "failed"
            record.last_error = f"{type(error).__name__}: {error}"[:16_384]
        return True


def process_inbox_work(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
    handler: WorkerHandler,
    worker_id: str | None = None,
    lease_seconds: int | None = None,
    heartbeat_interval_seconds: float | None = None,
    logger: Any = None,
) -> WorkerInboxResult:
    """Deduplicate long work without holding a database transaction open."""

    normalized = validate_event(event)
    owner = worker_id or default_worker_id()
    configured_lease = lease_seconds if lease_seconds is not None else int(
        os.getenv("INBOX_LEASE_SECONDS", "300")
    )
    lease = max(30, configured_lease)
    claim = claim_inbox_work(
        session_scope,
        consumer_group=consumer_group,
        event=normalized,
        source=source,
        worker_id=owner,
        lease_seconds=lease,
    )
    if claim is WorkerInboxResult.IN_PROGRESS:
        retry_after = float(os.getenv("INBOX_BUSY_RETRY_SECONDS", "5"))
        raise MessageDeferredError(
            f"inbox work already leased for {consumer_group}/{normalized['event_id']}",
            retry_after_seconds=retry_after,
        )
    if claim is WorkerInboxResult.DUPLICATE:
        return claim

    event_id = cast(str, normalized["event_id"])
    stop_heartbeat = threading.Event()
    lease_lost = threading.Event()
    interval = heartbeat_interval_seconds
    if interval is None:
        interval = max(1.0, min(60.0, lease / 3.0))

    def heartbeat() -> None:
        while not stop_heartbeat.wait(interval):
            try:
                renewed = renew_inbox_claim(
                    session_scope,
                    consumer_group=consumer_group,
                    event_id=event_id,
                    worker_id=owner,
                )
                if not renewed:
                    lease_lost.set()
                    return
            except Exception:
                if logger is not None:
                    logger.exception("Inbox heartbeat failed for %s", event_id)

    heartbeat_thread: threading.Thread | None = None
    if interval > 0:
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
    try:
        handler(normalized)
    except Exception as error:
        _finish_inbox_work(
            session_scope,
            consumer_group=consumer_group,
            event_id=event_id,
            worker_id=owner,
            error=error,
        )
        raise
    finally:
        stop_heartbeat.set()
        if heartbeat_thread is not None:
            heartbeat_thread.join()

    completed = _finish_inbox_work(
        session_scope,
        consumer_group=consumer_group,
        event_id=event_id,
        worker_id=owner,
        error=None,
    )
    if lease_lost.is_set() or not completed:
        raise InboxLeaseLostError(f"inbox lease lost for {consumer_group}/{event_id}")
    return WorkerInboxResult.PROCESSED


def make_inbox_work_handler(
    session_scope: SessionScope = get_session,
    *,
    consumer_group: str,
    message: Any,
    handler: WorkerHandler,
    worker_id: str | None = None,
    logger: Any = None,
) -> WorkerHandler:
    """Capture Kafka location and return a leased inbox handler."""

    source = message_location(message)

    def process(event: dict[str, Any]) -> None:
        process_inbox_work(
            session_scope,
            consumer_group=consumer_group,
            event=event,
            source=source,
            handler=handler,
            worker_id=worker_id,
            logger=logger,
        )

    return process
