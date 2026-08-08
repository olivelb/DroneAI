"""Transactional inbox/outbox primitives.

The module contains no Kafka or PostgreSQL client setup. Callers inject a
session scope and a publisher, which keeps the state machine unit-testable with
SQLite and in-memory doubles.
"""

from __future__ import annotations

import os
import socket
import threading
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, UTC
from enum import StrEnum
from typing import Any, cast
from collections.abc import Callable

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from shared.database import InboxEvent, OutboxEvent
from shared.event_contracts import validate_event
from shared.inbox_records import build_inbox_record
from shared.kafka_reliability import RetryPolicy, message_location

SessionScope = Callable[[], AbstractContextManager[Any]]
DomainHandler = Callable[[Any, dict[str, Any]], None]
Publisher = Callable[[str, dict[str, Any], str | None], None]


class InboxResult(StrEnum):
    PROCESSED = "processed"
    DUPLICATE = "duplicate"


def utc_now() -> datetime:
    return datetime.now(UTC)


def enqueue_outbox(
    session: Any,
    *,
    topic: str,
    event: dict[str, Any],
    key: str | None = None,
    now: datetime | None = None,
) -> OutboxEvent:
    normalized = validate_event(event)
    current_time = now or utc_now()
    existing = session.query(OutboxEvent).filter(OutboxEvent.event_id == normalized["event_id"]).first()
    if existing is not None:
        return cast(OutboxEvent, existing)
    record = OutboxEvent(
        event_id=normalized["event_id"],
        event_type=normalized["event_type"],
        topic=topic,
        message_key=key,
        payload=normalized,
        status="pending",
        available_at=current_time,
        created_at=current_time,
    )
    session.add(record)
    session.flush()
    return record


def _process_inbox(
    session: Any,
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
    handler: DomainHandler,
) -> InboxResult:
    normalized = validate_event(event)
    existing = (
        session.query(InboxEvent)
        .filter(
            InboxEvent.consumer_group == consumer_group,
            InboxEvent.event_id == normalized["event_id"],
        )
        .first()
    )
    if existing is not None and existing.status == "completed":
        return InboxResult.DUPLICATE

    record = existing or build_inbox_record(
        consumer_group=consumer_group,
        event=normalized,
        source=source,
    )
    if existing is None:
        session.add(record)
        session.flush()
    else:
        record.attempts += 1
        record.last_error = None

    handler(session, normalized)
    record.status = "completed"
    record.processed_at = utc_now()
    return InboxResult.PROCESSED


def process_inbox_transaction(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    event: dict[str, Any],
    source: dict[str, Any],
    handler: DomainHandler,
) -> InboxResult:
    """Run the inbox receipt and domain handler in one DB transaction."""

    try:
        with session_scope() as session:
            return _process_inbox(
                session,
                consumer_group=consumer_group,
                event=event,
                source=source,
                handler=handler,
            )
    except IntegrityError:
        # A concurrent replica may have inserted the same receipt after our
        # initial read. Only treat it as a duplicate once its transaction is
        # visible as completed.
        with session_scope() as session:
            existing = (
                session.query(InboxEvent)
                .filter(
                    InboxEvent.consumer_group == consumer_group,
                    InboxEvent.event_id == event.get("event_id"),
                )
                .first()
            )
            if existing is not None and existing.status == "completed":
                return InboxResult.DUPLICATE
        raise


def make_inbox_handler(
    session_scope: SessionScope,
    *,
    consumer_group: str,
    message: Any,
    handler: DomainHandler,
) -> Callable[[dict[str, Any]], None]:
    source = message_location(message)

    def process(event: dict[str, Any]) -> None:
        process_inbox_transaction(
            session_scope,
            consumer_group=consumer_group,
            event=event,
            source=source,
            handler=handler,
        )

    return process


def deliver_outbox_event(
    record: OutboxEvent,
    *,
    publisher: Publisher,
    retry_policy: RetryPolicy,
    now: datetime | None = None,
) -> bool:
    now = now or utc_now()
    record.attempts += 1
    try:
        publisher(record.topic, record.payload, record.message_key)
    except Exception as error:
        record.last_error = f"{type(error).__name__}: {error}"
        if record.attempts >= retry_policy.max_attempts:
            record.status = "dead"
            record.dead_at = now
        else:
            record.status = "failed"
            delay = retry_policy.delay_before(record.attempts)
            record.available_at = now + timedelta(seconds=delay)
        record.locked_at = None
        record.locked_by = None
        return False

    record.status = "published"
    record.published_at = now
    record.last_error = None
    record.locked_at = None
    record.locked_by = None
    return True


def dispatch_outbox_batch(
    session_scope: SessionScope,
    *,
    publisher: Publisher,
    worker_id: str,
    batch_size: int = 50,
    retry_policy: RetryPolicy | None = None,
    now: datetime | None = None,
    lease_seconds: int | None = None,
) -> dict[str, int]:
    policy = retry_policy or RetryPolicy.from_environment()
    current_time = now or utc_now()
    configured_lease = lease_seconds if lease_seconds is not None else int(os.getenv("OUTBOX_LEASE_SECONDS", "300"))
    lease_cutoff = current_time - timedelta(seconds=max(30, configured_lease))
    results = {"selected": 0, "published": 0, "failed": 0, "dead": 0}
    claimed: list[dict[str, Any]] = []

    # Claim with a short transaction, then release all row locks before the
    # potentially slow network publication. A crashed publisher is recovered
    # after the lease expires.
    with session_scope() as session:
        records = (
            session.query(OutboxEvent)
            .filter(
                or_(
                    and_(
                        OutboxEvent.status.in_(("pending", "failed")),
                        OutboxEvent.available_at <= current_time,
                    ),
                    and_(
                        OutboxEvent.status == "publishing",
                        OutboxEvent.locked_at <= lease_cutoff,
                    ),
                )
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .with_for_update(skip_locked=True)
            .limit(batch_size)
            .all()
        )
        results["selected"] = len(records)
        for record in records:
            record.status = "publishing"
            record.locked_at = current_time
            record.locked_by = worker_id
            claimed.append(
                {
                    "id": record.id,
                    "topic": record.topic,
                    "payload": record.payload,
                    "message_key": record.message_key,
                }
            )

    for item in claimed:
        publication_error: Exception | None = None
        try:
            publisher(
                item["topic"],
                item["payload"],
                item["message_key"],
            )
        except Exception as error:
            publication_error = error

        with session_scope() as session:
            record = session.query(OutboxEvent).filter(OutboxEvent.id == item["id"]).with_for_update().one()
            if record.status != "publishing" or record.locked_by != worker_id:
                continue
            if publication_error is None:
                record.attempts += 1
                record.status = "published"
                record.published_at = current_time
                record.last_error = None
                record.locked_at = None
                record.locked_by = None
                results["published"] += 1
            else:
                record.attempts += 1
                record.last_error = f"{type(publication_error).__name__}: {publication_error}"
                if record.attempts >= policy.max_attempts:
                    record.status = "dead"
                    record.dead_at = current_time
                    results["dead"] += 1
                else:
                    record.status = "failed"
                    delay = policy.delay_before(record.attempts)
                    record.available_at = current_time + timedelta(seconds=delay)
                    results["failed"] += 1
                record.locked_at = None
                record.locked_by = None
    return results


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_outbox_dispatcher(
    session_scope: SessionScope,
    *,
    publisher: Publisher,
    stop_event: threading.Event | None = None,
    worker_id: str | None = None,
    poll_interval_seconds: float = 1.0,
    batch_size: int = 50,
    logger: Any = None,
) -> None:
    stop_event = stop_event or threading.Event()
    worker_id = worker_id or default_worker_id()
    while not stop_event.is_set():
        try:
            result = dispatch_outbox_batch(
                session_scope,
                publisher=publisher,
                worker_id=worker_id,
                batch_size=batch_size,
            )
            if result["selected"] and logger is not None:
                logger.info("Outbox dispatch: %s", result)
        except Exception:
            if logger is not None:
                logger.exception("Outbox dispatcher batch failed")
        stop_event.wait(poll_interval_seconds)
