"""Status Kafka consumer and WebSocket fan-out."""

from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from contextlib import suppress
from typing import Any

from confluent_kafka import Consumer
from fastapi import WebSocket

from shared.config import (
    KAFKA_BROKER,
    TOPIC_DEAD_LETTER,
    TOPIC_STATUS,
)
from shared.database import get_session
from shared.inbox_outbox import InboxResult, process_inbox_transaction
from shared.kafka_reliability import (
    message_location,
    process_message,
    reliable_consumer_config,
)

from .messaging import get_producer
from .mission_state import apply_mission_state


JsonObject = dict[str, Any]


class StatusHub:
    def __init__(self, history_size: int = 300):
        self.history: deque[str] = deque(maxlen=history_size)
        self.connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.append(websocket)
        for message in self.history:
            await websocket.send_text(message)

    def disconnect(self, websocket: WebSocket) -> None:
        with suppress(ValueError):
            self.connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        failed: list[WebSocket] = []
        for connection in self.connections:
            try:
                await connection.send_text(message)
            except Exception:
                failed.append(connection)
        for connection in failed:
            self.disconnect(connection)

    def remember(self, event: JsonObject) -> str:
        payload = json.dumps(event)
        self.history.append(payload)
        return payload


status_hub = StatusHub()


def handle_status_message(
    *,
    consumer: Any,
    producer: Any,
    message: Any,
    hub: StatusHub,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    handled: dict[str, Any] = {}

    def persist(event: JsonObject) -> None:
        handled["event"] = event
        handled["inbox_result"] = process_inbox_transaction(
            get_session,
            consumer_group="dashboard-api",
            event=event,
            source=message_location(message),
            handler=apply_mission_state,
        )

    succeeded = process_message(
        consumer=consumer,
        producer=producer,
        message=message,
        consumer_group="dashboard-api",
        expected_type="status",
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=persist,
    )
    if not succeeded:
        return False
    if handled["inbox_result"] == InboxResult.DUPLICATE:
        return True
    payload = hub.remember(handled["event"])
    print(f"STATUS {payload}")
    future = asyncio.run_coroutine_threadsafe(
        hub.broadcast(payload),
        loop,
    )
    future.result(timeout=5)
    return True


def consume_status_events(
    loop: asyncio.AbstractEventLoop,
    *,
    hub: StatusHub = status_hub,
    consumer: Any | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    stop_event = stop_event or threading.Event()
    status_consumer = consumer or Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            "dashboard-api",
            offset_reset="latest",
        )
    )
    status_consumer.subscribe([TOPIC_STATUS])
    status_producer = get_producer()
    try:
        while not stop_event.is_set():
            try:
                message = status_consumer.poll(1.0)
                if message is None:
                    continue
                if message.error():
                    print(f"Kafka status consumer error: {message.error()}")
                    continue
                handle_status_message(
                    consumer=status_consumer,
                    producer=status_producer,
                    message=message,
                    hub=hub,
                    loop=loop,
                )
            except Exception as error:
                print(f"Kafka status consumer loop error: {error}")
    finally:
        status_consumer.close()
