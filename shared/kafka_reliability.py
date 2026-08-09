"""Kafka reliability primitives that can be tested without a broker."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from collections.abc import Callable

from confluent_kafka import TopicPartition

from shared.event_contracts import decode_event, make_event


class MessageDeferredError(RuntimeError):
    """Ask the consumer to retry the same offset without DLQ or commit."""

    def __init__(self, message: str, *, retry_after_seconds: float = 5.0) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, retry_after_seconds)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> RetryPolicy:
        return cls(
            max_attempts=max(1, int(os.getenv("KAFKA_RETRY_MAX_ATTEMPTS", "3"))),
            base_delay_seconds=max(0.0, float(os.getenv("KAFKA_RETRY_BASE_DELAY_SECONDS", "1"))),
            max_delay_seconds=max(0.0, float(os.getenv("KAFKA_RETRY_MAX_DELAY_SECONDS", "30"))),
        )

    def delay_before(self, attempt: int) -> float:
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * float(2 ** max(0, attempt - 1)),
        )


@dataclass
class ConsumerAssignmentWatchdog:
    """Detect a consumer that stopped rejoining its subscribed group."""

    timeout_seconds: float = 60.0
    _unassigned_since: float | None = None

    @classmethod
    def from_environment(cls) -> ConsumerAssignmentWatchdog:
        return cls(
            timeout_seconds=max(
                10.0,
                float(
                    os.getenv(
                        "KAFKA_CONSUMER_UNASSIGNED_TIMEOUT_SECONDS",
                        "60",
                    )
                ),
            )
        )

    def should_recreate(
        self,
        consumer: Any,
        *,
        now: float | None = None,
    ) -> bool:
        if consumer.assignment():
            self._unassigned_since = None
            return False
        observed_at = time.monotonic() if now is None else now
        if self._unassigned_since is None:
            self._unassigned_since = observed_at
            return False
        return observed_at - self._unassigned_since >= self.timeout_seconds

    def reset(self) -> None:
        self._unassigned_since = None


def recreate_unassigned_consumer(
    consumer: Any,
    watchdog: ConsumerAssignmentWatchdog,
    consumer_factory: Callable[[], Any],
    logger: Any,
    consumer_name: str,
) -> tuple[Any, bool]:
    """Replace a consumer that remained outside its group past the deadline."""

    if not watchdog.should_recreate(consumer):
        return consumer, False
    logger.warning(
        "Kafka %s consumer remained unassigned; recreating it",
        consumer_name,
    )
    consumer.close()
    watchdog.reset()
    return consumer_factory(), True


def reliable_consumer_config(
    broker: str,
    group_id: str,
    *,
    offset_reset: str,
    **extra: Any,
) -> dict[str, Any]:
    config = {
        "bootstrap.servers": broker,
        "group.id": group_id,
        "auto.offset.reset": offset_reset,
        "enable.auto.commit": False,
        "enable.auto.offset.store": False,
    }
    config.update(extra)
    return config


def commit_message(consumer: Any, message: Any) -> None:
    consumer.commit(message=message, asynchronous=False)


def seek_message(consumer: Any, message: Any) -> None:
    consumer.seek(
        TopicPartition(
            message.topic(),
            message.partition(),
            message.offset(),
        )
    )


def publish_json(
    producer: Any,
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
    delivery_timeout_seconds: float | None = None,
) -> None:
    """Publish one event and wait only for its delivery callback.

    Unlike ``flush()``, polling does not drain the producer's entire queue.
    Callers may therefore share a producer without one publication blocking on
    unrelated messages, while retaining a synchronous confirmation boundary
    before a consumed source offset is committed.
    """

    timeout = max(
        0.0,
        delivery_timeout_seconds
        if delivery_timeout_seconds is not None
        else float(os.getenv("KAFKA_DELIVERY_TIMEOUT_SECONDS", "30")),
    )
    delivered = False
    delivery_error: Any | None = None

    def on_delivery(error: Any | None, _message: Any) -> None:
        nonlocal delivered, delivery_error
        delivery_error = error
        delivered = True

    producer.produce(
        topic,
        key=key,
        value=json.dumps(payload),
        on_delivery=on_delivery,
    )
    deadline = time.monotonic() + timeout
    while not delivered:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"Kafka delivery confirmation timed out after {timeout:g}s for {topic}"
            )
        producer.poll(min(0.1, remaining))
    if delivery_error is not None:
        raise RuntimeError(f"Kafka delivery failed for {topic}: {delivery_error}")


def message_location(message: Any) -> dict[str, Any]:
    def call(name: str, default: Any = None) -> Any:
        value = getattr(message, name, None)
        return value() if callable(value) else default

    return {
        "topic": call("topic", "unknown"),
        "partition": call("partition"),
        "offset": call("offset"),
    }


def dead_letter_event(
    message: Any,
    *,
    consumer_group: str,
    expected_type: str,
    original_value: bytes | str,
    error: BaseException,
    attempts: int,
) -> dict[str, Any]:
    location = message_location(message)
    if isinstance(original_value, bytes):
        original_value = original_value.decode("utf-8", errors="replace")
    payload = {
        "source_topic": location["topic"],
        "source_partition": location["partition"],
        "source_offset": location["offset"],
        "consumer_group": consumer_group,
        "expected_event_type": expected_type,
        "attempts": attempts,
        "error": f"{type(error).__name__}: {error}",
        "original_value": original_value,
    }
    return make_event(
        "dead_letter",
        payload,
        correlation_id=f"{location['topic']}:{location['partition']}:{location['offset']}",
    )


def process_message(
    *,
    consumer: Any,
    producer: Any,
    message: Any,
    consumer_group: str,
    expected_type: str,
    dead_letter_topic: str,
    handler: Callable[[dict[str, Any]], None],
    retry_policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    logger: Any = None,
) -> bool:
    """Handle, retry, dead-letter, then commit one Kafka message.

    The offset is committed only after a successful handler or a confirmed
    dead-letter publication. If dead-letter publication fails, the offset is
    deliberately left uncommitted and the exception escapes.
    """

    policy = retry_policy or RetryPolicy.from_environment()
    original_value = message.value()
    last_error: BaseException | None = None
    attempts = 0
    for attempts in range(1, policy.max_attempts + 1):
        try:
            event = decode_event(original_value, expected_type=expected_type)
            # ``attempt`` is part of the producer's domain contract (for
            # example an AI campaign retry generation). Keep it stable across
            # local handler retries and expose the delivery retry separately.
            event["delivery_attempt"] = attempts - 1
            handler(event)
            commit_message(consumer, message)
            return True
        except MessageDeferredError as error:
            seek_message(consumer, message)
            if logger is not None:
                logger.info("Kafka message deferred without commit: %s", error)
            sleep(error.retry_after_seconds)
            return False
        except Exception as error:
            last_error = error
            if logger is not None:
                logger.warning(
                    "Kafka handler failed (%s/%s): %s",
                    attempts,
                    policy.max_attempts,
                    error,
                )
            if attempts < policy.max_attempts:
                sleep(policy.delay_before(attempts))

    assert last_error is not None
    dead_letter = dead_letter_event(
        message,
        consumer_group=consumer_group,
        expected_type=expected_type,
        original_value=original_value,
        error=last_error,
        attempts=attempts,
    )
    publish_json(
        producer,
        dead_letter_topic,
        dead_letter,
        key=dead_letter.get("correlation_id"),
    )
    commit_message(consumer, message)
    return False
