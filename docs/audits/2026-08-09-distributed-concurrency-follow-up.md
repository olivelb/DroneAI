# Distributed concurrency audit follow-up — 2026-08-09

## Baseline correction

The reviewed audit targeted `5fb2e05`. The implementation review started from
`51bbcb4`, after the fresh Example Quarry 2.0 E2E had already qualified direct
multipart ingestion, COLMAP, DroneGS, spatial coverage, COG/DSM publication,
5,412 tiles, referenced IA results and terminal aggregation. The release P0 in
the audit is therefore closed by
[the fresh E2E report](../benchmarks/example-quarry-fresh-e2e-2026-08-09.md).
A live multi-replica IA exercise remains a separate scale-out qualification.

## Implemented findings

| Finding | Resolution |
| --- | --- |
| Concurrent reservations of one dataset name | A partial unique database index reserves names whose session is `uploading` or `failed`. The API maps a racing insert to HTTP 409 before creating any multipart upload. |
| Cleanup races across API replicas | Expired rows are ordered and claimed with `FOR UPDATE SKIP LOCKED`. `NoSuchUpload` is an idempotent successful abort, covering a crash after the storage abort but before the database commit. |
| Legacy 50 GiB API-proxied upload | `/datasets/upload` now returns 404 in staging and production and remains available only for local development compatibility. |
| Ingress-dependent rate-limit identity | Raster buckets use the authenticated subject and ignore forwarded address headers. The database layer hashes the subject before persistence. |
| Mutable GitHub Actions references | The release workflow is SHA-pinned and one test now scans every workflow and rejects mutable external actions. |
| Incomplete Helm immutability guard | Immutable overlays accept only a 7–40 character lower-case Git SHA tag or an OCI SHA-256 digest. |
| Oversized IA result uploaded before rejection | IA and processing share one Helm limit. IA checks the serialized artifact size and rejects it before S3 publication. |
| Long finalization lease | Processing periodically renews ownership while loading referenced artifacts and forces renewal around deduplication and final publication. |

## Deliberately deferred qualifications

Per-file dataset SHA-256 is not recorded yet. A browser-side full-file digest
without a streaming implementation can allocate an entire multi-gigabyte file,
while server-side hashing would route the data back through FastAPI and defeat
direct ingestion. The next implementation must first qualify multipart checksum
semantics on both OVHcloud S3 and MinIO, or select and supply-chain-review a
streaming browser hasher. Size, exact part set and multipart ETag remain enforced.

The PostgreSQL rate-limit table still performs bounded oldest-row maintenance
when new subjects arrive. Replacing it with scheduled TTL cleanup remains a P3
large-cardinality optimization, not a single-tenant release blocker.

Removal of inline detection events and the remaining legacy pipeline requires a
declared compatibility deadline and is intentionally not folded into this
concurrency patch.

## Verification

- 575 CPU/non-integration tests passed; 13 GPU/integration tests were deselected;
- Ruff and all strict mypy ratchets passed;
- Markdown links, event schemas and platform version contracts passed;
- Compose configuration, Helm lint and immutable production rendering passed;
- mutable Helm tags were rejected;
- `actionlint` and the all-workflow immutable-action contract passed.
