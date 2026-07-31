import json

import pytest

from shared.kafka_reliability import (
    RetryPolicy,
    process_message,
    reliable_consumer_config,
)


class FakeMessage:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def topic(self):
        return "image-tiles"

    def partition(self):
        return 2

    def offset(self):
        return 42


class FakeConsumer:
    def __init__(self):
        self.commits = []

    def commit(self, *, message, synchronous):
        self.commits.append((message, synchronous))


class FakeProducer:
    def __init__(self, pending=0):
        self.pending = pending
        self.messages = []

    def produce(self, topic, *, key, value):
        self.messages.append((topic, key, json.loads(value)))

    def flush(self):
        return self.pending


def tile_message(*, attempt=0):
    return FakeMessage(
        json.dumps(
            {
                "vol_id": "mission-1",
                "tile_index": 3,
                "tile_s3_key": "missions/mission-1/tile_3.jpg",
                "attempt": attempt,
            }
        ).encode()
    )


def test_consumer_config_disables_automatic_offset_management():
    config = reliable_consumer_config(
        "kafka:9092",
        "workers",
        offset_reset="earliest",
        **{"max.poll.interval.ms": 123},
    )

    assert config["enable.auto.commit"] is False
    assert config["enable.auto.offset.store"] is False
    assert config["max.poll.interval.ms"] == 123


def test_process_message_retries_then_commits_after_success():
    consumer = FakeConsumer()
    producer = FakeProducer()
    calls = []
    sleeps = []

    def handler(event):
        calls.append((event["attempt"], event["delivery_attempt"]))
        if len(calls) < 3:
            raise RuntimeError("temporary")

    result = process_message(
        consumer=consumer,
        producer=producer,
        message=tile_message(attempt=7),
        consumer_group="ia-workers",
        expected_type="image_tile",
        dead_letter_topic="pipeline-dead-letter",
        handler=handler,
        retry_policy=RetryPolicy(3, 0.25, 1),
        sleep=sleeps.append,
    )

    assert result is True
    assert calls == [(7, 0), (7, 1), (7, 2)]
    assert sleeps == [0.25, 0.5]
    assert len(consumer.commits) == 1
    assert producer.messages == []


def test_process_message_dead_letters_poison_event_then_commits():
    consumer = FakeConsumer()
    producer = FakeProducer()

    result = process_message(
        consumer=consumer,
        producer=producer,
        message=tile_message(),
        consumer_group="ia-workers",
        expected_type="image_tile",
        dead_letter_topic="pipeline-dead-letter",
        handler=lambda _event: (_ for _ in ()).throw(RuntimeError("permanent")),
        retry_policy=RetryPolicy(2, 0, 0),
        sleep=lambda _delay: None,
    )

    assert result is False
    assert len(consumer.commits) == 1
    topic, key, dead_letter = producer.messages[0]
    assert topic == "pipeline-dead-letter"
    assert key == dead_letter["correlation_id"]
    assert dead_letter["event_type"] == "dead_letter"
    assert dead_letter["attempts"] == 2
    assert dead_letter["source_partition"] == 2
    assert dead_letter["source_offset"] == 42
    assert "RuntimeError: permanent" in dead_letter["error"]


def test_dead_letter_delivery_failure_leaves_offset_uncommitted():
    consumer = FakeConsumer()
    producer = FakeProducer(pending=1)

    with pytest.raises(RuntimeError, match="undelivered"):
        process_message(
            consumer=consumer,
            producer=producer,
            message=tile_message(),
            consumer_group="ia-workers",
            expected_type="image_tile",
            dead_letter_topic="pipeline-dead-letter",
            handler=lambda _event: (_ for _ in ()).throw(ValueError("bad")),
            retry_policy=RetryPolicy(1, 0, 0),
        )

    assert consumer.commits == []
