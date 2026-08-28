"""Cancellation and diagnostic logging shared by bounded COLMAP stages."""

from __future__ import annotations

import logging
from typing import Any

from shared.cancellation import DurableCancellationRegistry
from worker_support import WorkerCancellationState

logger = logging.getLogger("app1-colmap")
cancellation_state = WorkerCancellationState(DurableCancellationRegistry())


class PipelineCancelledError(Exception):
    """Raised when the active mission has been cancelled."""


def report_mission_progress(
    vol_id: str,
    step: str,
    progress: int | float,
    status: str = "processing",
    log: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Log stage diagnostics; the Stage Job publisher owns durable progress."""
    logger.info(
        "mission=%s step=%s progress=%s status=%s log=%s",
        vol_id, step, progress, status, log or "",
        extra={"stage_details": details},
    )


def ensure_not_cancelled(process: Any | None = None) -> None:
    try:
        cancellation_state.ensure_not_cancelled(process)
    except RuntimeError as error:
        raise PipelineCancelledError(str(error)) from error
