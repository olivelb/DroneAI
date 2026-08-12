"""Process runtime shared by pipeline stages and the Kafka worker."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from shared.cancellation import DurableCancellationRegistry
from worker_support import MissionStateTracker, WorkerCancellationState

logger = logging.getLogger("app1-colmap")
cancellation_state = WorkerCancellationState(DurableCancellationRegistry())
mission_state_tracker = MissionStateTracker()

ProgressReporter = Callable[..., None]
_producer: Any | None = None
_progress_reporter: ProgressReporter | None = None


class PipelineCancelledError(Exception):
    """Raised when the active mission has been cancelled."""


def configure_worker_runtime(producer: Any, progress_reporter: ProgressReporter) -> None:
    """Bind process-level messaging dependencies when the worker starts."""
    global _producer, _progress_reporter
    _producer = producer
    _progress_reporter = progress_reporter


def reset_worker_runtime() -> None:
    """Reset process-level dependencies for isolated tests."""
    global _producer, _progress_reporter
    _producer = None
    _progress_reporter = None


def require_producer() -> Any:
    if _producer is None:
        raise RuntimeError("COLMAP worker runtime is not configured")
    return _producer


def report_mission_progress(
    vol_id: str,
    step: str,
    progress: int | float,
    status: str = "processing",
    log: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    mission_state_tracker.record_progress(
        vol_id,
        step,
        progress,
        status=status,
        log=log,
        details=details,
    )
    if _progress_reporter is not None:
        _progress_reporter(
            vol_id,
            step,
            progress,
            status=status,
            log=log,
            details=details,
            organization_id=mission_state_tracker.active_organization_id(vol_id),
        )


def ensure_not_cancelled(process: Any | None = None) -> None:
    try:
        cancellation_state.ensure_not_cancelled(process)
    except RuntimeError as error:
        raise PipelineCancelledError(str(error)) from error
