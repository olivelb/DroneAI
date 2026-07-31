"""Kafka reliability primitives that can be tested without a broker."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from shared.event_contracts import decode_event, make_event


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0

    @classmethod
    def from_environment(cls) -> "RetryPolicy":
        return cls(
            max_attempts=max(1, int(os.getenv("KAFKA_RETRY_MAX_ATTEMPTS", "3"))),
            base_delay_seconds=max(
                0.0, float(os.getenv("KAFKA_RETRY_BASE_DELAY_SECONDS", "1"))
            ),
            max_delay_seconds=max(
                0.0, float(os.getenv("KAFKA_RETRY_MAX_DELAY_SECONDS", "30"))
            ),
        )

    def delay_before(self, attempt: int) -> float:
        return min(
            self.max_delay_seconds,
            self.base_delay_seconds * (2 ** max(0, attempt - 1)),
        )


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
    consumer.commit(message=message, synchronous=True)


def publish_json(
    producer: Any,
    topic: str,
    payload: dict[str, Any],
    *,
    key: str | None = None,
) -> None:
    producer.produce(topic, key=key, value=json.dumps(payload))
    pending = producer.flush()
    if pending:
        raise RuntimeError(f"Kafka producer still has {pending} undelivered messages")


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
