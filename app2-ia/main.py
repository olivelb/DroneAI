"""Kafka composition root for the AI tile worker."""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, Producer


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from sam3_backend import Sam3Backend
from shared.cancellation import DurableCancellationRegistry
from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_IMAGE_TILES,
    TOPIC_STATUS,
    TOPIC_TILE_DETECTIONS,
)
from shared.deployment_mode import assert_fused_compute_allowed
from shared.kafka_reliability import (
    ConsumerAssignmentWatchdog,
    process_message,
    recreate_unassigned_consumer,
    reliable_consumer_config,
)
from shared.worker_inbox import make_inbox_work_handler
from shared.worker_messaging import (
    make_cancellation_handler,
    make_progress_publisher,
    run_control_consumer,
)
from tile_detection_workflow import TileDetectionWorkflow


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app2-ia")

CONSUMER_GROUP = "ia-tile-workers"
CONTROL_CONSUMER_GROUP = "ia-control-workers"

producer = Producer({"bootstrap.servers": KAFKA_BROKER})
cancel_manager = DurableCancellationRegistry()
progress_publisher = make_progress_publisher(
    producer,
    TOPIC_STATUS,
    service_name="IA",
)


def report_progress(
    vol_id: str,
    step: str,
    progress: int,
    status: str = "processing",
    log: str | None = None,
) -> None:
    if log:
        print(f"[{step}] {log}")
    progress_publisher(
        vol_id,
        step,
        progress,
        status=status,
        log=log,
    )


workflow = TileDetectionWorkflow(
    producer=producer,
    output_topic=TOPIC_TILE_DETECTIONS,
    cancellation_registry=cancel_manager,
    progress_reporter=report_progress,
    sam3_backend=Sam3Backend(logger=logger),
    logger=logger,
)


def create_work_consumer() -> Consumer:
    consumer = Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            CONSUMER_GROUP,
            offset_reset="earliest",
        )
    )
    consumer.subscribe([TOPIC_IMAGE_TILES])
    return consumer


def control_consumer_thread() -> None:
    run_control_consumer(
        kafka_broker=KAFKA_BROKER,
        topic=TOPIC_CONTROL,
        consumer_group=CONTROL_CONSUMER_GROUP,
        producer=producer,
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=make_cancellation_handler(cancel_manager, logger),
        logger=logger,
    )


def process_tile(tile_info: dict[str, Any]) -> None:
    """Compatibility handler kept as the Kafka reliability boundary."""

    workflow.process_tile(tile_info)


def worker_main() -> None:
    assert_fused_compute_allowed("IA")
    work_consumer = create_work_consumer()
    assignment_watchdog = ConsumerAssignmentWatchdog.from_environment()
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    logger.info("App 2 (IA Workers) waiting for tiles on Kafka")
    try:
        while True:
            message = work_consumer.poll(1.0)
            work_consumer, recreated = recreate_unassigned_consumer(
                work_consumer, assignment_watchdog, create_work_consumer, logger, "tile"
            )
            if recreated:
                continue
            if message is None or message.error():
                continue
            process_message(
                consumer=work_consumer,
                producer=producer,
                message=message,
                consumer_group=CONSUMER_GROUP,
                expected_type="image_tile",
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=make_inbox_work_handler(
                    consumer_group=CONSUMER_GROUP,
                    message=message,
                    handler=process_tile,
                    logger=logger,
                ),
                logger=logger,
            )
    except KeyboardInterrupt:
        logger.info("Shutdown requested by user")
    finally:
        work_consumer.close()


if __name__ == "__main__":
    worker_main()
