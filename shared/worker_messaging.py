"""Reusable Kafka wiring for worker progress and cancellation messages."""

from __future__ import annotations

from typing import Any, Callable

from confluent_kafka import Consumer

from shared.event_contracts import make_event
from shared.kafka_reliability import (
    process_message,
    publish_json,
    reliable_consumer_config,
)


def make_progress_publisher(
    producer: Any,
    topic: str,
    *,
    service_name: str,
) -> Callable[..., None]:
    def publish(
        vol_id: str,
        step: str,
        progress: int,
        status: str = "processing",
        log: str | None = None,
        details: dict | None = None,
    ) -> None:
        event = make_event(
            "status",
            {
                "vol_id": vol_id,
                "step": step,
                "progress": progress,
                "status": status,
                "service": service_name,
            },
        )
        if log:
            event["log"] = log
        if details is not None:
            event["details"] = details
        publish_json(producer, topic, event, key=vol_id)

    return publish


def run_control_consumer(
    *,
    kafka_broker: str,
    topic: str,
    consumer_group: str,
    producer: Any,
    dead_letter_topic: str,
    handler: Callable[[dict], None],
    logger: Any,
    consumer_factory: Callable[[dict], Any] = Consumer,
) -> None:
    consumer = consumer_factory(
        reliable_consumer_config(
            kafka_broker,
            consumer_group,
            offset_reset="latest",
        )
    )
    consumer.subscribe([topic])
    while True:
        message = consumer.poll(1.0)
        if message is None or message.error():
            continue
        process_message(
            consumer=consumer,
            producer=producer,
            message=message,
            consumer_group=consumer_group,
            expected_type="control",
            dead_letter_topic=dead_letter_topic,
            handler=handler,
            logger=logger,
        )
