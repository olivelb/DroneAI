"""Standalone process for outbox, upload recovery, and stage scheduling."""

from __future__ import annotations

import logging
import signal
import threading

from shared.deployment_mode import bounded_stage_jobs_enabled
from shared.observability import start_metrics_server

from .control_leadership import (
    ControlLeadershipError,
    control_leader_election_enabled,
    control_leader_poll_seconds,
    try_acquire_control_leadership,
)
from .control_runtime import start_control_loops
from .control_worker_health import clear_heartbeat, record_heartbeat


logger = logging.getLogger("droneai.control-worker")


def _run_supervised_loops(stop_event: threading.Event) -> None:
    supervisor = start_control_loops(stop_event)
    try:
        record_heartbeat("single")
        while not stop_event.wait(5):
            supervisor.raise_if_unhealthy()
            record_heartbeat("single")
    finally:
        supervisor.stop()


def _run_elected_loops(
    process_stop_event: threading.Event,
    poll_seconds: float,
) -> None:
    while not process_stop_event.is_set():
        try:
            leadership = try_acquire_control_leadership()
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Control leadership acquisition failed")
            process_stop_event.wait(poll_seconds)
            continue
        if leadership is None:
            record_heartbeat("follower")
            process_stop_event.wait(poll_seconds)
            continue

        leader_stop_event = threading.Event()
        supervisor = None
        logger.info("Control worker acquired PostgreSQL leadership")
        try:
            supervisor = start_control_loops(leader_stop_event)
            record_heartbeat("leader")
            while not process_stop_event.wait(poll_seconds):
                leadership.raise_if_unhealthy()
                supervisor.raise_if_unhealthy()
                record_heartbeat("leader")
        except ControlLeadershipError:
            logger.exception("Control worker lost PostgreSQL leadership")
        finally:
            if supervisor is not None:
                supervisor.stop()
            leadership.release()
            logger.info("Control worker released PostgreSQL leadership")


def run(stop_event: threading.Event | None = None) -> None:
    process_stop_event = stop_event or threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("Control worker received signal %s", signum)
        process_stop_event.set()

    if stop_event is None:
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)

    # Every replica validates the protected deployment contract before it can
    # become a follower. A failover must never activate a misconfigured pod.
    bounded_stage_jobs_enabled()
    clear_heartbeat()
    metrics_server = start_metrics_server()
    try:
        if control_leader_election_enabled():
            _run_elected_loops(
                process_stop_event,
                control_leader_poll_seconds(),
            )
        else:
            _run_supervised_loops(process_stop_event)
    finally:
        if metrics_server is not None:
            metrics_server.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
