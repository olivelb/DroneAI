"""Standalone process for outbox, upload recovery, and stage scheduling."""

from __future__ import annotations

import logging
import signal
import threading

from .control_runtime import start_control_loops


logger = logging.getLogger("droneai.control-worker")


def run() -> None:
    stop_event = threading.Event()

    def request_stop(signum: int, _frame: object) -> None:
        logger.info("Control worker received signal %s", signum)
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    supervisor = start_control_loops(stop_event)
    try:
        while not stop_event.wait(5):
            supervisor.raise_if_unhealthy()
    finally:
        supervisor.stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
