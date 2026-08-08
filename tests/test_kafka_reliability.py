import json

import pytest

from shared.kafka_reliability import (
    MessageDeferredError,
    RetryPolicy,
    publish_json,
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
        self.seeks = []

    def commit(self, *, message, asynchronous):
        self.commits.append((message, asynchronous))

    def seek(self, position):
        self.seeks.append(position)


class FakeProducer:
    def __init__(self, delivery_error=None, *, deliver=True):
        self.delivery_error = delivery_error
        self.deliver = deliver
        self.messages = []
        self.callbacks = []
        self.polls = []

    def produce(self, topic, *, key, value, on_delivery):
        self.messages.append((topic, key, json.loads(value)))
        self.callbacks.append(on_delivery)

    def poll(self, timeout):
        self.polls.append(timeout)
        if self.deliver and self.callbacks:
            self.callbacks.pop(0)(self.delivery_error, None)
        return 0


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


def test_publish_json_confirms_only_its_delivery_with_poll():
    producer = FakeProducer()

    publish_json(producer, "tile-detections", {"value": 1}, key="tile-1")

    assert producer.messages == [("tile-detections", "tile-1", {"value": 1})]
    assert len(producer.polls) == 1


def test_publish_json_propagates_delivery_error_and_timeout():
    with pytest.raises(RuntimeError, match="delivery failed"):
        publish_json(
            FakeProducer(delivery_error="broker unavailable"),
            "tile-detections",
            {"value": 1},
        )

    with pytest.raises(TimeoutError, match="confirmation timed out"):
        publish_json(
            FakeProducer(deliver=False),
            "tile-detections",
            {"value": 1},
            delivery_timeout_seconds=0,
        )


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
    assert consumer.commits[0][1] is False
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
    assert consumer.commits[0][1] is False
    topic, key, dead_letter = producer.messages[0]
    assert topic == "pipeline-dead-letter"
    assert key == dead_letter["correlation_id"]
    assert dead_letter["event_type"] == "dead_letter"
    assert dead_letter["attempts"] == 2
    assert dead_letter["source_partition"] == 2
    assert dead_letter["source_offset"] == 42
    assert "RuntimeError: permanent" in dead_letter["error"]


def test_process_message_defers_without_commit_or_dead_letter():
    consumer = FakeConsumer()
    producer = FakeProducer()
    sleeps = []

    result = process_message(
        consumer=consumer,
        producer=producer,
        message=tile_message(),
        consumer_group="ia-workers",
        expected_type="image_tile",
        dead_letter_topic="pipeline-dead-letter",
        handler=lambda _event: (_ for _ in ()).throw(
            MessageDeferredError("claim is active", retry_after_seconds=0.25)
        ),
        retry_policy=RetryPolicy(3, 0, 0),
        sleep=sleeps.append,
    )

    assert result is False
    assert consumer.commits == []
    assert producer.messages == []
    assert sleeps == [0.25]
    assert len(consumer.seeks) == 1
    position = consumer.seeks[0]
    assert (position.topic, position.partition, position.offset) == (
        "image-tiles",
        2,
        42,
    )


def test_dead_letter_delivery_failure_leaves_offset_uncommitted():
    consumer = FakeConsumer()
    producer = FakeProducer(delivery_error="broker unavailable")

    with pytest.raises(RuntimeError, match="delivery failed"):
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
