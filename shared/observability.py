"""Low-cardinality operational metrics shared by DroneAI processes.

Scientific quality values deliberately do not belong here. These metrics
describe service traffic, durable queues, reconciliation, compute capacity and
external dependency failures.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from threading import Thread
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, start_http_server

_METRIC_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

HTTP_REQUESTS = Counter(
    "droneai_http_requests_total",
    "Dashboard API requests completed.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "droneai_http_request_duration_seconds",
    "Dashboard API request duration in seconds.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
OUTBOX_BATCH_EVENTS = Counter(
    "droneai_outbox_batch_events_total",
    "Durable outbox records observed during dispatcher batches.",
    ("outcome",),
)
OUTBOX_EVENTS = Gauge(
    "droneai_outbox_events",
    "Current durable outbox records by lifecycle status.",
    ("status",),
)
OUTBOX_OLDEST_UNPUBLISHED_AGE = Gauge(
    "droneai_outbox_oldest_unpublished_age_seconds",
    "Age in seconds of the oldest pending, failed or publishing outbox record.",
)
STAGE_RUNS = Gauge(
    "droneai_stage_runs",
    "Current bounded stage runs by state and resource class.",
    ("status", "resource_class"),
)
STAGE_RESOURCE_UNITS = Gauge(
    "droneai_stage_resource_units",
    "Current physical resource units by stage state and resource class.",
    ("status", "resource_class"),
)
STAGE_OLDEST_QUEUED_AGE = Gauge(
    "droneai_stage_oldest_queued_age_seconds",
    "Age in seconds of the oldest queued bounded stage run.",
)
RECONCILIATION_RECORDS = Counter(
    "droneai_reconciliation_records_total",
    "Records repaired or removed by a control-plane reconciliation pass.",
    ("loop", "outcome"),
)
CONTROL_LOOP_PASSES = Counter(
    "droneai_control_loop_passes_total",
    "Control-plane loop passes by outcome.",
    ("loop", "outcome"),
)
KAFKA_CONSUMER_LAG = Gauge(
    "droneai_kafka_consumer_lag",
    "Approximate Kafka records remaining after the last consumed offset.",
    ("consumer_group", "topic", "partition"),
)
KAFKA_CONSUMER_ERRORS = Counter(
    "droneai_kafka_consumer_errors_total",
    "Kafka consumer errors grouped by bounded error type.",
    ("consumer", "error_type"),
)
S3_OPERATION_FAILURES = Counter(
    "droneai_s3_operation_failures_total",
    "Failed S3-compatible storage operations.",
    ("operation", "error_type"),
)


def metrics_enabled() -> bool:
    raw = os.getenv("DRONEAI_METRICS_ENABLED", "false").strip().lower()
    if raw not in {"true", "false"}:
        raise RuntimeError("DRONEAI_METRICS_ENABLED must be true or false")
    return raw == "true"


def metrics_port() -> int:
    try:
        value = int(os.getenv("DRONEAI_METRICS_PORT", "9100"))
    except ValueError as error:
        raise RuntimeError("DRONEAI_METRICS_PORT must be an integer") from error
    if not 1024 <= value <= 65535:
        raise RuntimeError("DRONEAI_METRICS_PORT must be between 1024 and 65535")
    return value


@dataclass
class MetricsServer:
    """Graceful handle for the Prometheus client's daemon HTTP server."""

    server: Any
    thread: Thread

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def start_metrics_server() -> MetricsServer | None:
    """Expose this process registry on its pod-local metrics port."""

    if not metrics_enabled():
        return None
    server, thread = start_http_server(port=metrics_port(), addr="0.0.0.0")
    return MetricsServer(server=server, thread=thread)


def observe_http_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
    HTTP_REQUEST_DURATION.labels(method, route).observe(duration_seconds)


def observe_outbox_batch(results: dict[str, int]) -> None:
    for outcome in ("selected", "published", "failed", "dead"):
        count = max(0, int(results.get(outcome, 0)))
        if count:
            OUTBOX_BATCH_EVENTS.labels(outcome).inc(count)


def observe_outbox_queue(
    counts: dict[str, int],
    *,
    oldest_unpublished_age_seconds: float,
) -> None:
    OUTBOX_EVENTS.clear()
    for status, count in sorted(counts.items()):
        if _METRIC_NAME.fullmatch(status):
            OUTBOX_EVENTS.labels(status).set(max(0, count))
    OUTBOX_OLDEST_UNPUBLISHED_AGE.set(
        max(0.0, oldest_unpublished_age_seconds)
    )


def observe_stage_queue(
    counts: dict[tuple[str, str], tuple[int, int]],
    *,
    oldest_queued_age_seconds: float,
) -> None:
    STAGE_RUNS.clear()
    STAGE_RESOURCE_UNITS.clear()
    for (status, resource_class), (runs, units) in sorted(counts.items()):
        STAGE_RUNS.labels(status, resource_class).set(max(0, runs))
        STAGE_RESOURCE_UNITS.labels(status, resource_class).set(max(0, units))
    STAGE_OLDEST_QUEUED_AGE.set(max(0.0, oldest_queued_age_seconds))


def observe_reconciliation(loop: str, outcome: str, count: int) -> None:
    if count > 0:
        RECONCILIATION_RECORDS.labels(loop, outcome).inc(count)


def observe_control_loop(loop: str, *, succeeded: bool) -> None:
    CONTROL_LOOP_PASSES.labels(loop, "success" if succeeded else "failure").inc()


def observe_kafka_lag(
    consumer_group: str,
    topic: str,
    partition: int,
    lag: int,
) -> None:
    KAFKA_CONSUMER_LAG.labels(
        consumer_group,
        topic,
        str(partition),
    ).set(max(0, lag))


def observe_kafka_error(consumer: str, error: BaseException | object) -> None:
    error_type = type(error).__name__
    KAFKA_CONSUMER_ERRORS.labels(consumer, error_type).inc()


def observe_s3_failure(operation: str, error: BaseException) -> None:
    S3_OPERATION_FAILURES.labels(operation, type(error).__name__).inc()
