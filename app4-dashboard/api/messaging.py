"""Kafka publisher gateway for API commands and mission submissions."""

from __future__ import annotations

import threading
from typing import Any, Protocol, cast

from confluent_kafka import Producer

from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_MISSION,
)
from shared.event_contracts import (
    deterministic_tenant_event_id,
    make_event,
    tenant_correlation_id,
)
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
                _producer = Producer({"bootstrap.servers": KAFKA_BROKER})
    return _producer


def _tenant_payload(payload: JsonObject) -> tuple[JsonObject, str, str]:
    organization_id = validate_organization_id(
        str(payload.get("organization_id") or LEGACY_ORGANIZATION_ID)
    )
    vol_id = str(payload["vol_id"])
    return {**payload, "organization_id": organization_id}, organization_id, vol_id


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


def build_new_mission_event(payload: JsonObject) -> JsonObject:
    payload, organization_id, vol_id = _tenant_payload(payload)
    attempt = int(payload.get("attempt", 0))
    return cast(
        JsonObject,
        make_event(
            "mission",
            payload,
            event_id=deterministic_tenant_event_id(
                "mission", organization_id, vol_id, "start"
            ),
            correlation_id=tenant_correlation_id(organization_id, vol_id),
            attempt=attempt,
        ),
    )


def build_resume_event(payload: JsonObject) -> JsonObject:
    payload, organization_id, vol_id = _tenant_payload(payload)
    attempt = int(payload.get("attempt", 0))
    return cast(
        JsonObject,
        make_event(
            "mission",
            payload,
            event_id=deterministic_tenant_event_id(
                "mission",
                organization_id,
                vol_id,
                "resume",
                attempt,
            ),
            correlation_id=tenant_correlation_id(organization_id, vol_id),
            attempt=attempt,
        ),
    )


def build_stage_mission_event(payload: JsonObject) -> JsonObject:
    payload, organization_id, vol_id = _tenant_payload(payload)
    stage_run_id = str(payload["stage_run_id"])
    attempt = int(payload.get("attempt", 0))
    return cast(
        JsonObject,
        make_event(
            "mission",
            payload,
            event_id=deterministic_tenant_event_id(
                "mission",
                organization_id,
                vol_id,
                "stage",
                stage_run_id,
                attempt,
            ),
            correlation_id=tenant_correlation_id(organization_id, stage_run_id),
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


def publish_new_mission(
    payload: JsonObject,
    *,
    kafka_producer: Any | None = None,
) -> JsonObject:
    if kafka_producer is None:
        kafka_producer = get_producer()
    event = build_new_mission_event(payload)
    publish_json(
        kafka_producer,
        TOPIC_MISSION,
        event,
        key=tenant_mission_key(event["organization_id"], event["vol_id"]),
    )
    return event


def publish_resume(
    payload: JsonObject,
    *,
    kafka_producer: Any | None = None,
) -> JsonObject:
    if kafka_producer is None:
        kafka_producer = get_producer()
    event = build_resume_event(payload)
    publish_json(
        kafka_producer,
        TOPIC_MISSION,
        event,
        key=tenant_mission_key(event["organization_id"], event["vol_id"]),
    )
    return event
