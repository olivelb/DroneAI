"""Kafka composition root for orthomosaic tiling and detection aggregation."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, Producer


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from analysis_workflow import AnalysisWorkflow
from legacy_aggregation import LegacyAggregationWorkflow, dedupe_configured
from orthomosaic_tiler import OrthomosaicTiler
from processing_dispatcher import ProcessingDispatcher
from shared.cancellation import DurableCancellationRegistry
from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_IMAGE_TILES,
    TOPIC_ORTHO,
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


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app3-processing")

CONSUMER_GROUP = "processing-group"
CONTROL_CONSUMER_GROUP = "processing-control-workers"

producer = Producer({"bootstrap.servers": KAFKA_BROKER})
progress_publisher = make_progress_publisher(
    producer,
    TOPIC_STATUS,
    service_name="TILER",
)
ia_progress_publisher = make_progress_publisher(
    producer,
    TOPIC_STATUS,
    service_name="IA",
)
cancel_manager = DurableCancellationRegistry()


analysis_workflow = AnalysisWorkflow(
    producer=producer,
    orthomosaic_topic=TOPIC_ORTHO,
    tile_topic=TOPIC_IMAGE_TILES,
    dedupe=dedupe_configured,
    logger=logger,
)
orthomosaic_tiler = OrthomosaicTiler(
    producer=producer,
    tile_topic=TOPIC_IMAGE_TILES,
    is_cancelled=cancel_manager.is_cancelled,
    report_progress=progress_publisher,
    logger=logger,
)
legacy_workflow = LegacyAggregationWorkflow(
    report_progress=progress_publisher,
    report_ia_progress=ia_progress_publisher,
    logger=logger,
)
dispatcher = ProcessingDispatcher(
    orthomosaic_topic=TOPIC_ORTHO,
    cancellation_registry=cancel_manager,
    tiler=orthomosaic_tiler,
    analysis_workflow=analysis_workflow,
    legacy_workflow=legacy_workflow,
)


def create_work_consumer() -> Consumer:
    consumer = Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            CONSUMER_GROUP,
            offset_reset="earliest",
            **{"max.poll.interval.ms": 7_200_000},
        )
    )
    consumer.subscribe([TOPIC_ORTHO, TOPIC_TILE_DETECTIONS])
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


def event_handler(topic: str) -> Callable[[dict[str, Any]], None]:
    def handle(data: dict[str, Any]) -> None:
        dispatcher.process_event(data, topic)

    return handle


def worker_main() -> None:
    assert_fused_compute_allowed("processing")
    work_consumer = create_work_consumer()
    assignment_watchdog = ConsumerAssignmentWatchdog.from_environment()
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    logger.info("App 3 (Tiler/Aggregator) waiting for Kafka events")
    last_recovery = 0.0
    try:
        while True:
            message = work_consumer.poll(1.0)
            work_consumer, recreated = recreate_unassigned_consumer(
                work_consumer, assignment_watchdog, create_work_consumer, logger, "processing"
            )
            if recreated:
                continue
            if time.monotonic() - last_recovery >= 60:
                dispatcher.recover()
                last_recovery = time.monotonic()
            if message is None or message.error():
                continue
            topic = message.topic()
            expected_type = "orthomosaic" if topic == TOPIC_ORTHO else "tile_detection"
            process_message(
                consumer=work_consumer,
                producer=producer,
                message=message,
                consumer_group=CONSUMER_GROUP,
                expected_type=expected_type,
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=make_inbox_work_handler(
                    consumer_group=CONSUMER_GROUP,
                    message=message,
                    handler=event_handler(topic),
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
