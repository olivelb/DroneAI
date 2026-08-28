import json

import pytest

from shared.event_contracts import make_event
from shared.kafka_reliability import (
    ConsumerAssignmentWatchdog,
    RetryPolicy,
    publish_json,
    recreate_unassigned_consumer,
    process_message,
    reliable_consumer_config,
)


class FakeMessage:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def topic(self):
        return "pipeline-control"

    def partition(self):
        return 2

    def offset(self):
        return 42


class FakeConsumer:
    def __init__(self):
        self.commits = []
        self.seeks = []
        self.assignments = []
        self.closed = False

    def commit(self, *, message, asynchronous):
        self.commits.append((message, asynchronous))

    def seek(self, position):
        self.seeks.append(position)

    def assignment(self):
        return self.assignments

    def close(self):
        self.closed = True


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


def control_message(*, attempt=0, organization_id=None):
    payload = {
        "vol_id": "mission-1",
        "command": "cancel",
        "attempt": attempt,
    }
    if organization_id is not None:
        payload["organization_id"] = organization_id
    return FakeMessage(
        json.dumps(make_event("control", payload, attempt=attempt)).encode()
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


def test_assignment_watchdog_never_recycles_a_legitimately_idle_consumer():
    consumer = FakeConsumer()
    watchdog = ConsumerAssignmentWatchdog(timeout_seconds=60)

    assert watchdog.should_recreate(consumer, now=100) is False
    assert watchdog.should_recreate(consumer, now=10_000) is False


def test_assignment_watchdog_recovers_only_after_assignment_loss_timeout():
    consumer = FakeConsumer()
    watchdog = ConsumerAssignmentWatchdog(timeout_seconds=60)

    consumer.assignments = [object()]
    assert watchdog.should_recreate(consumer, now=100) is False

    consumer.assignments = []
    assert watchdog.should_recreate(consumer, now=101) is False
    assert watchdog.should_recreate(consumer, now=160) is False
    assert watchdog.should_recreate(consumer, now=161) is True

    consumer.assignments = [object()]
    assert watchdog.should_recreate(consumer, now=162) is False

    consumer.assignments = []
    assert watchdog.should_recreate(consumer, now=200) is False
    watchdog.reset()
    assert watchdog.should_recreate(consumer, now=10_000) is False


def test_recreate_unassigned_consumer_closes_and_replaces_stalled_member():
    consumer = FakeConsumer()
    replacement = FakeConsumer()
    watchdog = ConsumerAssignmentWatchdog(timeout_seconds=0)
    consumer.assignments = [object()]
    watchdog.should_recreate(consumer, now=100)
    consumer.assignments = []
    watchdog.should_recreate(consumer, now=100)

    result, recreated = recreate_unassigned_consumer(
        consumer,
        watchdog,
        lambda: replacement,
        logger=type("Logger", (), {"warning": lambda *_args: None})(),
        consumer_name="tile",
        now=100,
    )

    assert recreated is True
    assert result is replacement
    assert consumer.closed is True
    result, recreated = recreate_unassigned_consumer(
        replacement,
        watchdog,
        lambda: FakeConsumer(),
        logger=type("Logger", (), {"warning": lambda *_args: None})(),
        consumer_name="tile",
        now=10_000,
    )
    assert recreated is False
    assert result is replacement


def test_publish_json_confirms_only_its_delivery_with_poll():
    producer = FakeProducer()

    publish_json(producer, "pipeline-status", {"value": 1}, key="tile-1")

    assert producer.messages == [("pipeline-status", "tile-1", {"value": 1})]
    assert len(producer.polls) == 1


def test_publish_json_propagates_delivery_error_and_timeout():
    with pytest.raises(RuntimeError, match="delivery failed"):
        publish_json(
            FakeProducer(delivery_error="broker unavailable"),
            "pipeline-status",
            {"value": 1},
        )

    with pytest.raises(TimeoutError, match="confirmation timed out"):
        publish_json(
            FakeProducer(deliver=False),
            "pipeline-status",
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
        message=control_message(attempt=7),
        consumer_group="ia-workers",
        expected_type="control",
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
        message=control_message(organization_id="tenant-a"),
        consumer_group="ia-workers",
        expected_type="control",
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
    assert dead_letter["organization_id"] == "tenant-a"
    assert "RuntimeError: permanent" in dead_letter["error"]


def test_dead_letter_delivery_failure_leaves_offset_uncommitted():
    consumer = FakeConsumer()
    producer = FakeProducer(delivery_error="broker unavailable")

    with pytest.raises(RuntimeError, match="delivery failed"):
        process_message(
            consumer=consumer,
            producer=producer,
            message=control_message(),
            consumer_group="ia-workers",
            expected_type="control",
            dead_letter_topic="pipeline-dead-letter",
            handler=lambda _event: (_ for _ in ()).throw(ValueError("bad")),
            retry_policy=RetryPolicy(1, 0, 0),
        )

    assert consumer.commits == []
