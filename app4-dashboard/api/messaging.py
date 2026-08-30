"""Kafka publisher gateway for cancellation and durable control events."""

from __future__ import annotations

import threading
from typing import Any, Protocol, cast

from confluent_kafka import Producer

from shared.config import TOPIC_CONTROL
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
from shared.kafka_connection import kafka_connection_settings
from shared.kafka_partitioning import tenant_mission_key
from shared.kafka_reliability import publish_json
from shared.tenancy import LEGACY_ORGANIZATION_ID, validate_organization_id

JsonObject = dict[str, Any]


class ProducerProtocol(Protocol):
    def produce(
        self,
        topic: str,
        *,
        key: str | None,
        value: str,
    ) -> None: ...

    def flush(self) -> int: ...


_producer: ProducerProtocol | None = None
_producer_lock = threading.Lock()


def get_producer() -> ProducerProtocol:
    global _producer
    if _producer is None:
        with _producer_lock:
            if _producer is None:
                _producer = Producer(kafka_connection_settings().client_config())
    return _producer


def build_cancel_event(
    vol_id: str,
    *,
    organization_id: str = LEGACY_ORGANIZATION_ID,
    attempt: int = 0,
) -> JsonObject:
    organization_id = validate_organization_id(organization_id)
    return cast(
        JsonObject,
        make_event(
            "control",
            {
                "vol_id": vol_id,
                "organization_id": organization_id,
                "command": "cancel",
            },
            event_id=deterministic_tenant_event_id(
                "control", organization_id, vol_id, "cancel", attempt
            ),
            correlation_id=tenant_correlation_id(organization_id, vol_id),
            attempt=attempt,
        ),
    )


def publish_outbox_event(
    topic: str,
    payload: JsonObject,
    key: str | None,
) -> None:
    publish_json(get_producer(), topic, payload, key=key)


def publish_cancel(
    vol_id: str,
    *,
    organization_id: str = LEGACY_ORGANIZATION_ID,
    attempt: int = 0,
    kafka_producer: Any | None = None,
) -> None:
    if kafka_producer is None:
        kafka_producer = get_producer()
    event = build_cancel_event(
        vol_id,
        organization_id=organization_id,
        attempt=attempt,
    )
    publish_json(
        kafka_producer,
        TOPIC_CONTROL,
        event,
        key=tenant_mission_key(organization_id, vol_id),
    )
