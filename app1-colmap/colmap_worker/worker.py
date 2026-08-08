"""Kafka worker bootstrap with no network side effects at import time."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_MISSION,
    TOPIC_STATUS,
)
from shared.kafka_reliability import process_message
from shared.worker_inbox import make_inbox_work_handler
from worker_support import (
    build_mission_context,
    control_consumer_loop,
    create_consumer,
    create_producer,
    log_mission_start,
    make_progress_reporter,
)

from . import runtime
from .mission_runner import run_colmap_pipeline

logger = logging.getLogger("app1-colmap")


def control_consumer_thread(producer: object) -> None:
    control_consumer_loop(
        KAFKA_BROKER,
        TOPIC_CONTROL,
        runtime.cancellation_state.cancel,
        logger,
        producer,
        TOPIC_DEAD_LETTER,
    )


def worker_main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    producer = create_producer(KAFKA_BROKER)
    runtime.configure_worker_runtime(
        producer,
        make_progress_reporter(producer, TOPIC_STATUS, service_name="COLMAP"),
    )
    threading.Thread(target=control_consumer_thread, args=(producer,), daemon=True).start()
    consumer = create_consumer(KAFKA_BROKER, TOPIC_MISSION)

    print("🎧 App 1 (COLMAP 4 — ALIKED/GLOMAP) ready.")

    def process_mission(mission: dict[str, Any]) -> None:
        mission_context = None
        try:
            mission_context = build_mission_context(mission)
            runtime.cancellation_state.start_mission(
                mission_context.vol_id,
                int(mission.get("attempt", 0)),
            )
            previous_state = runtime.mission_state_tracker.start_mission(mission_context)

            log_mission_start(mission_context)
            if previous_state:
                resume_progress = previous_state.get("progress")
                if not isinstance(resume_progress, (int, float)):
                    resume_progress = 0
                resume_message = (
                    "Resuming from saved workspace state: "
                    f"status={previous_state.get('status', 'unknown')}, "
                    f"step={previous_state.get('step', 'unknown')}, "
                    f"progress={int(resume_progress)}%"
                )
                previous_log = previous_state.get("last_log")
                if previous_log:
                    resume_message += f", last_log={previous_log}"
                print(f"↻ {resume_message}")
                runtime.report_mission_progress(
                    mission_context.vol_id,
                    "RESUMING",
                    int(resume_progress),
                    log=resume_message,
                    details={
                        "event": "resume_detected",
                        "previous_state": {
                            "status": previous_state.get("status"),
                            "step": previous_state.get("step"),
                            "progress": previous_state.get("progress"),
                            "updated_at": previous_state.get("updated_at"),
                            "last_log": previous_state.get("last_log"),
                        },
                    },
                )

            if not mission_context.input_dir:
                raise ValueError(f"No input dataset specified for mission {mission_context.vol_id}")

            run_colmap_pipeline(
                mission_context.work_dir,
                mission_context.input_dir,
                mission_context.vol_id,
                mission_context.mission,
            )
        finally:
            if mission_context is not None:
                runtime.mission_state_tracker.clear_mission(mission_context.vol_id)
            runtime.cancellation_state.clear()

    try:
        while True:
            message = consumer.poll(1.0)
            if message is None or message.error():
                continue
            process_message(
                consumer=consumer,
                producer=producer,
                message=message,
                consumer_group="colmap-workers-v4",
                expected_type="mission",
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=make_inbox_work_handler(
                    consumer_group="colmap-workers-v4",
                    message=message,
                    handler=process_mission,
                    logger=logger,
                ),
                logger=logger,
            )
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        runtime.reset_worker_runtime()
