"""Status Kafka consumer and WebSocket fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from functools import partial
from time import monotonic
from typing import Any

from confluent_kafka import Consumer, TopicPartition
from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from shared.config import (
    TOPIC_DEAD_LETTER,
    TOPIC_STATUS,
)
from shared.database import get_mission_audience, get_session
from shared.inbox_outbox import process_inbox_transaction
from shared.kafka_connection import kafka_connection_settings
from shared.kafka_reliability import (
    message_location,
    process_message,
    reliable_consumer_config,
)
from shared.observability import observe_kafka_error, observe_kafka_lag

from .messaging import get_producer
from .mission_state import apply_mission_state
from .security import WebSocketAuthorization, websocket_authorization_status


JsonObject = dict[str, Any]
STATUS_STATE_INBOX_GROUP = "dashboard-api-status-state"
logger = logging.getLogger("droneai.realtime")


def _positive_setting(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if value < 1:
        raise RuntimeError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class ConnectionIdentity:
    audience: str
    organization_id: str
    credential_id: str
    peer: str


def status_consumer_group(instance_id: str | None = None) -> str:
    """Return a stable group unique to one API pod for broadcast fan-out."""

    identity = (instance_id or socket.gethostname()).strip()
    if not identity:
        raise RuntimeError("Dashboard API realtime instance identity is empty")
    return f"dashboard-api-realtime-{identity}"


class StatusHub:
    def __init__(
        self,
        history_size: int = 300,
        *,
        max_history_audiences: int | None = None,
        max_history_messages: int | None = None,
        max_connections: int | None = None,
        max_connections_per_organization: int | None = None,
        max_connections_per_credential: int | None = None,
        max_connections_per_peer: int | None = None,
    ):
        self.history_size = history_size
        self.max_history_audiences = max_history_audiences or _positive_setting(
            "DRONEAI_WS_MAX_HISTORY_AUDIENCES", 10_000
        )
        self.max_history_messages = max_history_messages or _positive_setting(
            "DRONEAI_WS_MAX_HISTORY_MESSAGES", 10_000
        )
        self.max_connections = max_connections or _positive_setting(
            "DRONEAI_WS_MAX_CONNECTIONS", 10_000
        )
        self.max_connections_per_organization = (
            max_connections_per_organization
            or _positive_setting("DRONEAI_WS_MAX_CONNECTIONS_PER_ORGANIZATION", 500)
        )
        self.max_connections_per_credential = (
            max_connections_per_credential
            or _positive_setting("DRONEAI_WS_MAX_CONNECTIONS_PER_CREDENTIAL", 20)
        )
        self.max_connections_per_peer = max_connections_per_peer or _positive_setting(
            "DRONEAI_WS_MAX_CONNECTIONS_PER_PEER", 100
        )
        self.history: OrderedDict[str, deque[str]] = OrderedDict()
        self._history_messages = 0
        self.connections: dict[WebSocket, ConnectionIdentity] = {}
        self._history_lock = threading.Lock()

    def _at_connection_limit(self, identity: ConnectionIdentity) -> bool:
        values = list(self.connections.values())
        return (
            len(values) >= self.max_connections
            or sum(item.organization_id == identity.organization_id for item in values)
            >= self.max_connections_per_organization
            or sum(item.credential_id == identity.credential_id for item in values)
            >= self.max_connections_per_credential
            or sum(item.peer == identity.peer for item in values)
            >= self.max_connections_per_peer
        )

    async def connect(
        self,
        websocket: WebSocket,
        owner_subject: str,
        *,
        organization_id: str = "legacy-unassigned",
        credential_id: str | None = None,
        peer: str = "unknown",
    ) -> bool:
        identity = ConnectionIdentity(
            audience=owner_subject,
            organization_id=organization_id,
            credential_id=credential_id or owner_subject,
            peer=peer,
        )
        if self._at_connection_limit(identity):
            await websocket.close(code=4429, reason="WebSocket connection quota exceeded")
            return False
        await websocket.accept()
        self.connections[websocket] = identity
        with self._history_lock:
            history = list(self.history.get(owner_subject, ()))
        for message in history:
            await websocket.send_text(message)
        return True

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.pop(websocket, None)

    async def broadcast(self, message: str, owner_subject: str) -> None:
        failed: list[WebSocket] = []
        for connection, identity in list(self.connections.items()):
            if identity.audience != owner_subject:
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
            history = self.history.setdefault(
                owner_subject,
                deque(maxlen=self.history_size),
            )
            previous_size = len(history)
            history.append(payload)
            self._history_messages += len(history) - previous_size
            self.history.move_to_end(owner_subject)
            while (
                len(self.history) > self.max_history_audiences
                or self._history_messages > self.max_history_messages
            ):
                _audience, evicted = self.history.popitem(last=False)
                self._history_messages -= len(evicted)
        return payload


status_hub = StatusHub()


async def serve_status_connection(
    websocket: WebSocket,
    authorization: WebSocketAuthorization,
    *,
    hub: StatusHub = status_hub,
) -> None:
    principal = authorization.principal
    audience = f"{principal.organization_id}:{principal.subject}"
    connected = await hub.connect(
        websocket,
        audience,
        organization_id=principal.organization_id,
        credential_id=principal.credential_id or principal.member_id,
        peer=authorization.peer,
    )
    if not connected:
        return

    revalidate_seconds = _positive_setting("DRONEAI_WS_REVALIDATE_SECONDS", 45)
    idle_seconds = _positive_setting("DRONEAI_WS_IDLE_TIMEOUT_SECONDS", 300)
    max_message_bytes = _positive_setting("DRONEAI_WS_MAX_MESSAGE_BYTES", 4096)
    last_activity = monotonic()
    last_validation = last_activity
    try:
        while True:
            now = monotonic()
            wait_seconds = max(
                0.1,
                min(
                    revalidate_seconds - (now - last_validation),
                    idle_seconds - (now - last_activity),
                ),
            )
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=wait_seconds,
                )
                if len(message.encode("utf-8")) > max_message_bytes:
                    await websocket.close(code=1009, reason="WebSocket message too large")
                    return
                last_activity = monotonic()
            except TimeoutError:
                pass
            except WebSocketDisconnect:
                return

            now = monotonic()
            if now - last_activity >= idle_seconds:
                await websocket.close(code=4408, reason="WebSocket idle timeout")
                return
            if now - last_validation < revalidate_seconds:
                continue
            validation = await run_in_threadpool(
                websocket_authorization_status,
                authorization,
            )
            if validation != "valid":
                await websocket.close(
                    code=4401 if validation == "unauthenticated" else 4403,
                    reason=(
                        "Authentication expired"
                        if validation == "unauthenticated"
                        else "Authorization changed"
                    ),
                )
                return
            last_validation = now
            await websocket.send_text('{"type":"ping"}')
    finally:
        hub.disconnect(websocket)


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
            kafka_connection_settings(),
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
