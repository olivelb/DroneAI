# Operational observability contract v1

This contract covers SaaS availability, isolation infrastructure and control
plane reliability. It does not turn reconstruction accuracy, sharpness, GCP
quality or detector performance into platform SLOs. Those remain scientific
dataset qualifications.

## Exposure boundary

The dashboard API and the elected control worker expose Prometheus text on a
dedicated pod port, `9100` by default. The port is annotated for pod discovery
and is not added to the public dashboard service or ingress. Staging and
production Helm renders must enable it; development keeps it opt-in.

HTTP series use route templates rather than customer-controlled paths. Metrics
never label an organization, member, mission, dataset, object key, credential
or raw request identifier. `X-Request-ID` is instead validated or generated,
returned to the caller and included in failed-request logs. Pipeline events
retain their existing tenant correlation identifiers.

## Signals

| Signal | Meaning |
|---|---|
| `droneai_http_requests_total` | requests by method, route template and status |
| `droneai_http_request_duration_seconds` | request latency histogram |
| `droneai_outbox_events` | durable outbox depth by state |
| `droneai_outbox_oldest_unpublished_age_seconds` | age of the oldest pending, failed or publishing event |
| `droneai_stage_runs` | logical bounded runs by state/resource class |
| `droneai_stage_resource_units` | physical units represented by those runs |
| `droneai_stage_oldest_queued_age_seconds` | oldest bounded-run queue wait |
| `droneai_reconciliation_records_total` | upload/retention records repaired or removed |
| `droneai_control_loop_passes_total` | success/failure of durable loop passes |
| `droneai_kafka_consumer_lag` | remaining status records after the consumed offset |
| `droneai_kafka_consumer_errors_total` | status-consumer failures |
| `droneai_s3_operation_failures_total` | failed S3-compatible client calls |

The standard Python process, garbage-collector and runtime metrics exported by
`prometheus-client` are retained.

## Initial alert objectives

The optional `PrometheusRule` can be enabled when the Prometheus Operator CRDs
are installed. The staging and production examples enable metric collection
but deliberately leave this cluster-level CRD opt-in. Alert defaults are
intentionally explicit:

- HTTP 5xx ratio above 5% for 10 minutes: critical;
- HTTP p95 above 2 seconds for 15 minutes: warning;
- oldest publishable outbox event above 300 seconds: critical;
- any dead outbox event: critical;
- oldest queued stage above 600 seconds: warning;
- any failed control-loop pass in 10 minutes: critical;
- Kafka lag above 1,000 records for 10 minutes: warning;
- any S3 operation failure in 10 minutes: warning.

These are an initial operational baseline, not contractual customer SLAs.
They must be recalibrated from OVH preproduction traffic without coupling them
to dataset-backed scientific acceptance thresholds.
