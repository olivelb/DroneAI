"""Status Kafka consumer and WebSocket fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
from collections import deque
from functools import partial
from typing import Any

from confluent_kafka import Consumer, TopicPartition
from fastapi import WebSocket

from shared.config import (
    KAFKA_BROKER,
    TOPIC_DEAD_LETTER,
    TOPIC_STATUS,
)
from shared.database import get_mission_audience, get_session
from shared.inbox_outbox import process_inbox_transaction
from shared.kafka_reliability import (
    message_location,
    process_message,
    reliable_consumer_config,
)
from shared.observability import observe_kafka_error, observe_kafka_lag

from .messaging import get_producer
from .mission_state import apply_mission_state


JsonObject = dict[str, Any]
STATUS_STATE_INBOX_GROUP = "dashboard-api-status-state"
logger = logging.getLogger("droneai.realtime")


def status_consumer_group(instance_id: str | None = None) -> str:
    """Return a stable group unique to one API pod for broadcast fan-out."""

    identity = (instance_id or socket.gethostname()).strip()
    if not identity:
        raise RuntimeError("Dashboard API realtime instance identity is empty")
    return f"dashboard-api-realtime-{identity}"


class StatusHub:
    def __init__(self, history_size: int = 300):
        self.history: deque[tuple[str, str]] = deque(maxlen=history_size)
        self.connections: dict[WebSocket, str] = {}
        self._history_lock = threading.Lock()

    async def connect(self, websocket: WebSocket, owner_subject: str) -> None:
        await websocket.accept()
        self.connections[websocket] = owner_subject
        with self._history_lock:
            history = list(self.history)
        for owner, message in history:
            if owner == owner_subject:
                await websocket.send_text(message)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.pop(websocket, None)

    async def broadcast(self, message: str, owner_subject: str) -> None:
        failed: list[WebSocket] = []
        for connection, connection_owner in list(self.connections.items()):
            if connection_owner != owner_subject:
                continue
            try:
                await connection.send_text(message)
            except Exception:
                failed.append(connection)
        for connection in failed:
            self.disconnect(connection)

    def remember(self, event: JsonObject, owner_subject: str) -> str:
        payload = json.dumps(event)
        with self._history_lock:
            self.history.append((owner_subject, payload))
        return payload


status_hub = StatusHub()


def mission_owner_subject(
    vol_id: str,
    organization_id: str | None = None,
) -> str:
    if not vol_id:
        return "legacy-unassigned"
    audience = get_mission_audience(vol_id, organization_id)
    if audience is None:
        if organization_id is None:
            raise LookupError("Legacy status event does not match a legacy mission")
        raise LookupError(
            "Status event organization does not match its durable mission"
        )
    return f"{audience[0]}:{audience[1]}"


def handle_status_message(
    *,
    consumer: Any,
    producer: Any,
    message: Any,
    hub: StatusHub,
    loop: asyncio.AbstractEventLoop,
    consumer_group: str = "dashboard-api-realtime",
) -> bool:
    def persist_and_broadcast(event: JsonObject) -> None:
        vol_id = str(event.get("vol_id") or "")
        event_organization_id = event.get("organization_id")
        organization_id = (
            str(event_organization_id) if event_organization_id is not None else None
        )
        owner_subject = mission_owner_subject(vol_id, organization_id)
        organization_id = (
            organization_id or owner_subject.partition(":")[0] or "legacy-unassigned"
        )
        process_inbox_transaction(
            partial(get_session, organization_id=organization_id),
            consumer_group=STATUS_STATE_INBOX_GROUP,
            event=event,
            source=message_location(message),
            handler=apply_mission_state,
        )
        payload = hub.remember(event, owner_subject)
        logger.debug("Broadcasting tenant status event %s", payload)
        future = asyncio.run_coroutine_threadsafe(
            hub.broadcast(payload, owner_subject),
            loop,
        )
        future.result(timeout=5)

    succeeded = process_message(
        consumer=consumer,
        producer=producer,
        message=message,
        consumer_group=consumer_group,
        expected_type="status",
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=persist_and_broadcast,
    )
    return bool(succeeded)


def consume_status_events(
    loop: asyncio.AbstractEventLoop,
    *,
    hub: StatusHub = status_hub,
    consumer: Any | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    stop_event = stop_event or threading.Event()
    consumer_group = status_consumer_group(os.getenv("POD_NAME"))
    status_consumer = consumer or Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            consumer_group,
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
                    observe_kafka_error("status", message.error())
                    logger.error("Kafka status consumer error: %s", message.error())
                    continue
                succeeded = handle_status_message(
                    consumer=status_consumer,
                    producer=status_producer,
                    message=message,
                    hub=hub,
                    loop=loop,
                    consumer_group=consumer_group,
                )
                if succeeded:
                    try:
                        topic = str(message.topic())
                        partition = int(message.partition())
                        _, high = status_consumer.get_watermark_offsets(
                            TopicPartition(topic, partition),
                            cached=True,
                        )
                        observe_kafka_lag(
                            consumer_group,
                            topic,
                            partition,
                            int(high) - int(message.offset()) - 1,
                        )
                    except Exception:
                        # Lag is an operational signal only: a broker/client
                        # telemetry failure must never affect message handling.
                        logger.debug(
                            "Kafka lag unavailable for the current consumer",
                            exc_info=True,
                        )
            except Exception as error:
                observe_kafka_error("status", error)
                logger.exception("Kafka status consumer loop error")
    finally:
        status_consumer.close()
