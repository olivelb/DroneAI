# Fault and concurrency qualification v1

This contract qualifies operational recovery independently from scientific
datasets and GPU result quality. Its scenarios use synthetic identifiers,
events and object bytes against real PostgreSQL, Kafka and S3-compatible
services where the boundary matters.

## Required invariants

| Fault or race | Required outcome | Qualification |
|---|---|---|
| control leader connection is lost | follower acquires the PostgreSQL session lock | real two-connection leadership test |
| duplicate Kafka delivery reaches two replicas | one durable handler completes and the other observes a duplicate | concurrent real-PostgreSQL inbox test |
| outbox process exits after durable claim | no early takeover; replacement publishes after lease expiry | `SystemExit` fault injection with real PostgreSQL |
| two callers finalize one upload | one manifest and one catalog row are published; both callers converge on `done` | concurrent row-lock test with real PostgreSQL |
| S3 times out during manifest publication | durable `finalizing` intent survives and a retry completes it | timeout injection across separate DB transactions |
| application revision rolls across a schema upgrade | current stable models work on `head-1`, and their data survives upgrade to `head` | Alembic rolling-compatibility CI drill |

Unit tests continue to cover heartbeat renewal, lost inbox ownership, stale
worker takeover, partial S3 deletion, multipart abort, CAS conflicts and
scheduler advisory locks. The real-service composition additionally exercises
mission launch/cancellation through HTTP, durable outbox publication and
cleanup against PostgreSQL, Kafka and MinIO.

This is an engineering release gate. Dataset-backed reconstruction accuracy,
GCP quality, detector recall and GPU performance remain separate scientific
qualifications.
