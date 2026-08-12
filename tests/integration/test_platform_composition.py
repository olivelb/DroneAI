"""Synthetic integration across the durable platform boundaries."""

from __future__ import annotations

import json
import importlib
import time
from uuid import uuid4

import pytest
from confluent_kafka import Consumer

from shared import storage
from shared.config import KAFKA_BROKER, TOPIC_STATUS
from shared.database import OutboxEvent, get_session
from shared.event_contracts import make_event, validate_event
from shared.inbox_outbox import dispatch_outbox_batch, enqueue_outbox


@pytest.mark.integration
def test_postgres_outbox_kafka_and_s3_compose_as_one_delivery_path(tmp_path) -> None:
    """Prove real DB, broker, and object-store clients compose without mocks."""

    unique_id = uuid4().hex
    mission_id = f"integration-{unique_id[:16]}"
    event = make_event(
        "status",
        {
            "vol_id": mission_id,
            "step": "INTEGRATION",
            "progress": 1,
            "status": "processing",
            "service": "INTEGRATION",
        },
    )
    event_id = str(event["event_id"])
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BROKER,
            "group.id": f"droneai-integration-{unique_id}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([TOPIC_STATUS])

    source = tmp_path / "payload.json"
    downloaded = tmp_path / "downloaded.json"
    source.write_text(json.dumps(event, sort_keys=True), encoding="utf-8")
    object_key = f"integration/{unique_id}/payload.json"
    outbox_created = False
    object_created = False

    try:
        assignment_deadline = time.monotonic() + 10
        while not consumer.assignment() and time.monotonic() < assignment_deadline:
            consumer.poll(0.2)
        assert consumer.assignment(), "Kafka consumer did not receive an assignment"

        with get_session() as session:
            enqueue_outbox(
                session,
                topic=TOPIC_STATUS,
                event=event,
                key=mission_id,
            )
        outbox_created = True

        messaging = importlib.import_module("app4-dashboard.api.messaging")

        result = dispatch_outbox_batch(
            get_session,
            publisher=messaging.publish_outbox_event,
            worker_id=f"integration-{unique_id}",
        )
        assert result == {"selected": 1, "published": 1, "failed": 0, "dead": 0}

        received = None
        receive_deadline = time.monotonic() + 15
        while received is None and time.monotonic() < receive_deadline:
            message = consumer.poll(0.5)
            if message is None or message.error():
                continue
            candidate = json.loads(message.value().decode("utf-8"))
            if candidate.get("event_id") == event_id:
                received = validate_event(candidate)
        assert received == validate_event(event)

        publication = storage.upload_verified_file(source, object_key)
        object_created = True
        assert publication["size"] == source.stat().st_size
        storage.download_file(object_key, downloaded)
        assert downloaded.read_bytes() == source.read_bytes()

        with get_session() as session:
            record = session.query(OutboxEvent).filter(OutboxEvent.event_id == event_id).one()
            assert record.status == "published"
            assert record.attempts == 1
    finally:
        consumer.close()
        if object_created:
            storage.delete_object(object_key)
        if outbox_created:
            with get_session() as session:
                session.query(OutboxEvent).filter(OutboxEvent.event_id == event_id).delete()
