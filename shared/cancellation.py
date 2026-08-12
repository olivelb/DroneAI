"""Attempt-scoped local and durable cancellation state for Kafka workers."""

from __future__ import annotations

import os
import threading
import time
from contextlib import AbstractContextManager
from datetime import datetime, UTC
from typing import Any
from collections.abc import Callable, Hashable

from shared.database import AIAnalysisRun, Mission, get_session


SessionScope = Callable[[], AbstractContextManager[Any]]


class AttemptCancellationRegistry:
    """Keep cancellation scoped to one mission/run generation.

    Kafka can deliver work from an older campaign attempt after a retry has
    started. Including the producer's attempt in the key prevents an old
    cancellation from poisoning the new generation.
    """

    def __init__(self) -> None:
        self._cancelled: set[
            tuple[Hashable | None, Hashable, Hashable | None, int]
        ] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _key(
        vol_id: Hashable,
        run_id: Hashable | None,
        attempt: int,
        organization_id: Hashable | None = None,
    ) -> tuple[Hashable | None, Hashable, Hashable | None, int]:
        return organization_id, vol_id, run_id, int(attempt)

    def cancel(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> None:
        with self._lock:
            self._cancelled.add(
                self._key(vol_id, run_id, attempt, organization_id)
            )

    def clear(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> None:
        with self._lock:
            self._cancelled.discard(
                self._key(vol_id, run_id, attempt, organization_id)
            )

    def is_cancelled(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> bool:
        with self._lock:
            return (
                self._key(vol_id, run_id, attempt, organization_id)
                in self._cancelled
            )


def _cancellation_record(
    session: Any,
    *,
    vol_id: str,
    run_id: str | None,
    organization_id: str | None = None,
    for_update: bool = False,
) -> Any | None:
    if run_id is None:
        query = session.query(Mission).filter(Mission.vol_id == vol_id)
    else:
        query = session.query(AIAnalysisRun).filter(
            AIAnalysisRun.vol_id == vol_id,
            AIAnalysisRun.run_id == run_id,
        )
    if organization_id is not None:
        if run_id is None:
            query = query.filter(Mission.organization_id == organization_id)
        else:
            query = query.join(Mission, AIAnalysisRun.mission_id == Mission.id).filter(
                Mission.organization_id == organization_id
            )
    if for_update:
        query = query.with_for_update()
    return query.first()


def mark_cancellation_requested(
    session: Any,
    *,
    vol_id: str,
    run_id: str | None = None,
    attempt: int = 0,
    organization_id: str | None = None,
) -> bool:
    """Persist cancellation when it still targets the current generation."""

    record = _cancellation_record(
        session,
        vol_id=vol_id,
        run_id=run_id,
        organization_id=organization_id,
        for_update=True,
    )
    if record is None or int(record.retry_count or 0) != int(attempt):
        return False
    now = datetime.now(UTC)
    record.status = "cancelled"
    record.error_message = None
    if run_id is None:
        record.current_step = "CANCELLATION_REQUESTED"
        record.updated_at = now
    else:
        record.phase = "cancelled"
        record.heartbeat_at = now
    return True


class DurableCancellationRegistry:
    """Share cancellation through PostgreSQL while retaining a fast local cache."""

    def __init__(
        self,
        session_scope: SessionScope = get_session,
        *,
        local: AttemptCancellationRegistry | None = None,
        poll_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_scope = session_scope
        self._local = local or AttemptCancellationRegistry()
        configured_poll = poll_seconds
        if configured_poll is None:
            configured_poll = float(os.getenv("CANCELLATION_POLL_SECONDS", "2"))
        self._poll_seconds = max(0.0, configured_poll)
        self._clock = clock
        self._last_checks: dict[
            tuple[Hashable | None, Hashable, Hashable | None, int], float
        ] = {}
        self._checks_lock = threading.Lock()

    def cancel(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> None:
        mission_id = str(vol_id)
        analysis_id = str(run_id) if run_id is not None else None
        with self._session_scope() as session:
            persisted = mark_cancellation_requested(
                session,
                vol_id=mission_id,
                run_id=analysis_id,
                attempt=attempt,
                organization_id=(
                    str(organization_id)
                    if organization_id is not None
                    else None
                ),
            )
        if not persisted:
            raise LookupError(
                "cancellation target is missing or no longer on attempt "
                f"{attempt}: {mission_id}/{analysis_id or 'mission'}"
            )
        self._local.cancel(
            vol_id,
            run_id,
            attempt,
            organization_id=organization_id,
        )
        with self._checks_lock:
            self._last_checks.pop(
                AttemptCancellationRegistry._key(
                    vol_id,
                    run_id,
                    attempt,
                    organization_id,
                ),
                None,
            )

    def clear(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> None:
        # Durable cancellation is immutable for one attempt. A retry increments
        # the generation, so clearing only the process-local cache is safe.
        self._local.clear(
            vol_id,
            run_id,
            attempt,
            organization_id=organization_id,
        )
        with self._checks_lock:
            self._last_checks.pop(
                AttemptCancellationRegistry._key(
                    vol_id,
                    run_id,
                    attempt,
                    organization_id,
                ),
                None,
            )

    def is_cancelled(
        self,
        vol_id: Hashable,
        run_id: Hashable | None = None,
        attempt: int = 0,
        *,
        organization_id: Hashable | None = None,
    ) -> bool:
        if self._local.is_cancelled(
            vol_id,
            run_id,
            attempt,
            organization_id=organization_id,
        ):
            return True
        key = AttemptCancellationRegistry._key(
            vol_id,
            run_id,
            attempt,
            organization_id,
        )
        checked_at = self._clock()
        with self._checks_lock:
            last_check = self._last_checks.get(key)
            if (
                last_check is not None
                and checked_at - last_check < self._poll_seconds
            ):
                return False
        mission_id = str(vol_id)
        analysis_id = str(run_id) if run_id is not None else None
        with self._session_scope() as session:
            record = _cancellation_record(
                session,
                vol_id=mission_id,
                run_id=analysis_id,
                organization_id=(
                    str(organization_id)
                    if organization_id is not None
                    else None
                ),
            )
            cancelled = (
                record is not None
                and record.status == "cancelled"
                and int(record.retry_count or 0) == int(attempt)
            )
        with self._checks_lock:
            self._last_checks[key] = checked_at
        if cancelled:
            self._local.cancel(
                vol_id,
                run_id,
                attempt,
                organization_id=organization_id,
            )
        return cancelled
