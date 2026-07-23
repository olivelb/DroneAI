"""Kafka publisher gateway for API commands and mission submissions."""

from __future__ import annotations

import time
import threading
from typing import Any

from confluent_kafka import Producer

from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_MISSION,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.kafka_reliability import publish_json


_producer = None
_producer_lock = threading.Lock()


def get_producer():
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = Producer({"bootstrap.servers": KAFKA_BROKER})
    return _producer


def publish_cancel(
    vol_id: str,
    *,
    kafka_producer: Any | None = None,
) -> None:
    if kafka_producer is None:
        kafka_producer = get_producer()
    event = make_event("control", {"vol_id": vol_id, "command": "cancel"})
    publish_json(kafka_producer, TOPIC_CONTROL, event, key=vol_id)


def publish_new_mission(
    payload: dict,
    *,
    kafka_producer: Any | None = None,
) -> dict:
    if kafka_producer is None:
        kafka_producer = get_producer()
    vol_id = payload["vol_id"]
    event = make_event(
        "mission",
        payload,
        event_id=deterministic_event_id(
            "mission",
            vol_id,
            time.time_ns(),
        ),
        correlation_id=vol_id,
    )
    publish_json(kafka_producer, TOPIC_MISSION, event, key=vol_id)
    return event


def publish_resume(
    payload: dict,
    *,
    kafka_producer: Any | None = None,
) -> dict:
    if kafka_producer is None:
        kafka_producer = get_producer()
    vol_id = payload["vol_id"]
    event = make_event(
        "mission",
        payload,
        event_id=deterministic_event_id("mission", vol_id, "resume"),
        correlation_id=vol_id,
    )
    publish_json(kafka_producer, TOPIC_MISSION, event, key=vol_id)
    return event
