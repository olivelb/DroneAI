"""Lifecycle supervision for durable dashboard control-plane loops."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass

from shared.database import get_session
from shared.deployment_mode import bounded_stage_jobs_enabled
from shared.inbox_outbox import run_outbox_dispatcher

from . import dataset_uploads
from .retention import run_retention_cleanup
from .messaging import publish_outbox_event
from .stage_orchestrator import start_stage_orchestrator


logger = logging.getLogger("droneai.control-runtime")


def embedded_control_loops_enabled() -> bool:
    raw = os.getenv("DRONEAI_EMBED_CONTROL_LOOPS", "true").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError("DRONEAI_EMBED_CONTROL_LOOPS must be true or false")
    return raw == "true"


@dataclass
class ControlLoopSupervisor:
    stop_event: threading.Event
    threads: tuple[threading.Thread, ...]

    def raise_if_unhealthy(self) -> None:
        stopped = [thread.name for thread in self.threads if not thread.is_alive()]
        if stopped and not self.stop_event.is_set():
            raise RuntimeError(
                "Control loop stopped unexpectedly: " + ", ".join(stopped)
            )

    def stop(self, timeout_seconds: float = 2.0) -> None:
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=timeout_seconds)


def start_control_loops(
    stop_event: threading.Event | None = None,
) -> ControlLoopSupervisor:
    # Validate before any daemon thread starts so a protected deployment cannot
    # become partially live with its compute scheduler disabled.
    bounded_stage_jobs_enabled()
    event = stop_event or threading.Event()
    outbox_thread = threading.Thread(
        target=run_outbox_dispatcher,
        args=(get_session,),
        kwargs={
            "publisher": publish_outbox_event,
            "stop_event": event,
            "logger": logger,
        },
        daemon=True,
        name="outbox-dispatcher",
    )
    upload_cleanup_thread = threading.Thread(
        target=dataset_uploads.run_upload_cleanup,
        args=(event,),
        daemon=True,
        name="dataset-upload-reconciler",
    )
    retention_thread = threading.Thread(
        target=run_retention_cleanup,
        args=(event,),
        daemon=True,
        name="organization-retention",
    )
    outbox_thread.start()
    upload_cleanup_thread.start()
    retention_thread.start()
    threads = [outbox_thread, upload_cleanup_thread, retention_thread]
    stage_thread = start_stage_orchestrator(event)
    if stage_thread is not None:
        threads.append(stage_thread)
    return ControlLoopSupervisor(event, tuple(threads))
