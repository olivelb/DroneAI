# Platform audit follow-up — 2026-08-08

## Scope

This note verifies the prioritized findings from the audit of `main` at
`c01218daf3d4bef76ffcea4572e3b690b2b7f687`. It records the successive
implementation batches, not a claim that every roadmap item is complete.

## Verified findings

| Audit item | Verification | Initial disposition |
|---|---|---|
| Multi-replica IA terminal status | Confirmed. `TileDetectionWorkflow` used a process-local counter, so no replica could reach the mission-wide tile count after Kafka partitioning. | Fixed in this batch. |
| Mutable production application tags | Confirmed. The generic production overlay inherited five `latest` tags from chart defaults. | Fixed in this batch. |
| Fresh E2E after the 5 m Gaussian default | Confirmed outstanding by the existing Example Quarry report. | Deferred to an explicit BIGZEN release qualification. |
| Browser-to-S3 multipart upload | Confirmed architectural improvement over the former 50 GiB FastAPI multipart boundary. | Implemented in the second batch. |
| Spatial Gaussian coverage gate | Confirmed. Primitive retention alone cannot prove footprint coverage. | Implemented as `GAUSSIAN_MAP_COVERAGE_V1`; fresh GPU calibration remains a release task. |
| Strict typing for `gaussian_ortho` | Confirmed absent from the current mypy ratchet. | Sixteen modules now run under strict mypy, including explicit COLMAP binary, EXIF/GPS, Gaussian-model and orthophoto-renderer boundaries. |
| Pre-approved hashes for dynamic YOLO variants | Confirmed absent; runtime hashes provide provenance but not prior approval. | Implemented for all eight supported OBB variants from the pinned upstream release. |
| Kafka tile-result payload references | Confirmed useful for bounding segmentation messages. | Implemented with a versioned, hash-bound S3 artifact and migration `0010`. |
| Coverage floor above 50% | Confirmed. The full non-GPU/non-integration suite now measures 61% branch coverage. | Raised from 50% to 55% with retained headroom. |

The audit's lower-priority documentation observations were also correct: the
development guide still referred to a dual-version Python suite after the
Python 3.12 normalization, and the README's short MIT statement did not expose
the GPL status of the specified DroneGS units and combined native binary.

## Durable IA completion contract

IA tile workers now have a deliberately narrow responsibility:

1. download one tile;
2. run inference and geolocation;
3. publish one idempotent tile result;
4. delete the local tile immediately.

They no longer infer global progress or terminal success from process-local
memory. The processing aggregator owns that decision because it serializes the
mission row, records every result in `ProcessedTile`, and counts zero-detection
tiles as completed work.

The aggregator publishes IA progress from this durable global count at the
first tile, every ten tiles and the final tile. It publishes the terminal
`IA/success` event only after vector generation, verified object upload and the
database transition to `aggregation_status=completed`. Recovery finalization
uses the same path, so a processing replica crash cannot lose the logical IA
terminal event permanently.

This removes the historical single-replica assumption without adding a second
distributed counter.

## Immutable production image contract

Local chart defaults retain `latest` for the image-import development flow.
The generic production overlay now:

- enables `global.requireImmutableImages`;
- supplies `REPLACE_GIT_SHA` for all five application services;
- fails Helm rendering if an application falls back to `latest`;
- accepts an `@sha256:` repository reference as the strongest immutable form.

CI renders the production overlay and rejects any remaining `:latest`
application reference. The OVH preproduction overlay keeps its existing
immutable-tag and cost guards.

## Validation

Validation completed on Ubuntu WSL2 with Python 3.12:

- focused distributed-worker, durable-aggregation, direct-upload, Helm and
  spatial-coverage contract tests passed;
- the full non-GPU/non-integration suite passed: 561 selected tests, with 13
  explicitly deselected;
- branch coverage measured 61%, retaining six points of headroom above the 55%
  enforced floor;
- Ruff, strict mypy domains, ShellCheck, actionlint, Markdown links and event
  schema checks passed;
- `pip-audit` reported no known vulnerabilities;
- all 9 frontend tests, ESLint, the optimized Next.js build, dependency audit
  and duplication gate passed;
- Helm lint and the production render passed, while the negative render using
  a mutable `latest` application tag failed as required.

The fresh GPU E2E qualification remains intentionally separate: it requires
BIGZEN, the Example Quarry benchmark and the long CUDA/COLMAP pipeline. It is
not part of the routine PR gate.

## Direct dataset ingestion contract

Mission Studio no longer proxies dataset bytes through FastAPI. The API now
creates a durable, owner-bound upload session after enforcing the existing
file-count, per-file, aggregate-size and extension limits. It creates one S3
multipart upload per file and issues short-lived, part-specific `PUT` URLs.

The browser uploads up to four parts per file and three files concurrently,
retries a failed part with a fresh URL, and returns the S3 ETags. The API accepts
completion only when every expected part is present exactly once, verifies the
remote object size and publishes a versioned dataset manifest after every file
has completed. Failed browser batches are aborted, while an API cleanup worker
reclaims expired multipart storage.

Upload session ownership is persisted in PostgreSQL, and production bucket
CORS is limited to configured frontend origins with `ETag` exposed. The legacy
proxied endpoint remains as a transitional compatibility surface only.

The direct-upload regression suite exercises durable ownership, quota
rejection, exact part completion, object-size verification helpers, manifest
publication, CORS contracts and the browser-to-S3 request sequence. The
frontend unit suite, lint and optimized Next.js build also pass.

## Spatial Gaussian product gate

Aerial Gaussian rendering now measures the finite DSM pixels inside a
conservative projected footprint derived from registered camera centres. A
16-by-16 grid makes local holes visible instead of allowing a good global
average to conceal them. The versioned `GAUSSIAN_MAP_COVERAGE_V1` policy checks:

- at least 50% valid pixels over the expected footprint;
- at least 75% of expected cells reaching 25% local coverage;
- at least 1% coverage in the worst expected cell;
- at least 10% at the tenth percentile of camera-containing cells.

The renderer writes `gaussian_coverage_report.json` atomically before GeoTIFF
publication. A rejected enforced gate stops publication; a deliberately
disabled gate still records a `measured-rejected` report. Empty tiles and
missing depth are NaN/NoData rather than zero-height observations. The verified
product manifest embeds the coverage summary and publication requires the
report for every aerial product. Facade output remains outside this map-only
contract.

Unit tests cover complete, sparse, locally punctured and collinear-camera
footprints. Coverage, filter policy, model filtering, GeoTIFF writing, height
referencing, partitioning, PCA alignment and scene assembly are now part of the
strict-mypy and service-core Ruff ratchets. The model-filtering protocol keeps
dynamic CuPy arrays isolated at the CUDA boundary while statically checking the
model lifecycle, camera arrays, callbacks and return identity. A fresh Example
Quarry GPU E2E is still required to calibrate the conservative coverage defaults
against a complete post-change product; routine PR CI must not run that long
qualification.

## Pre-approved YOLO checkpoint registry

All eight supported YOLO26/YOLO11 OBB variants now resolve through a typed
registry containing the upstream repository, release, exact asset URL and
SHA-256 digest. The values are pinned to the signed Ultralytics assets
`v8.4.0` release and agree with the digest already used for the baked
`yolo26l-obb.pt` image asset.

An existing cached checkpoint is verified before Ultralytics or Torch can
deserialize it. A freshly downloaded mismatch is removed and rejected. A
runtime release override cannot silently escape the registry; an arbitrary
`AERIAL_MODEL_FILE` requires an explicit 64-character
`AERIAL_CUSTOM_MODEL_SHA256` and a non-empty custom revision. The model
manifest continues to record the observed artifact hash, so prior approval and
post-run provenance are both retained.

## S3-referenced AI tile results

IA workers no longer place an unbounded segmentation result inside Kafka.
Each tile is serialized as a versioned JSON artifact under the deterministic
`missions/<vol>/ai-tile-results/<run-or-pipeline>/attempt_<n>/tile_<n>.json`
key, uploaded with verification, then represented in Kafka only by its S3 key,
SHA-256, exact byte size, schema version and detection count. The event remains
small even when masks or polygons are detailed.

Both the modern analysis workflow and the legacy mission workflow enforce the
same trust boundary before accepting a result: deterministic key, configured
size ceiling, exact object length, SHA-256, mission/run/tile/attempt identity,
model manifest and detection count. Modern tile receipts persist the key, hash,
size and producing attempt through Alembic migration `0010`, so finalization
re-verifies the correct inputs after a restart or a partial campaign retry.
Aggregate byte and detection limits remain in force during finalization.

The schema temporarily continues to accept inline `detections` for a rolling
upgrade, but an event must contain exactly one complete representation. New IA
workers publish references only; the compatibility path converts an inline
result into the same versioned S3 artifact before journaling it. An in-place
deployment therefore pauses IA, migrates and rolls processing/API consumers,
then rolls and resumes IA; an old consumer cannot safely read the new form.

## Callback-confirmed Kafka publication

Single-event publication no longer calls `flush()` and waits for every queued
producer record. It attaches a delivery callback to the specific event and
drives callbacks with bounded `poll()` calls until that record is acknowledged,
rejected or reaches the configurable `KAFKA_DELIVERY_TIMEOUT_SECONDS` deadline.

This preserves the important source-side invariant: `process_message()` commits
the consumed offset only after its handler's output has been confirmed. The
dead-letter path uses the same primitive, so a failed or timed-out DLQ delivery
still leaves the poison source offset uncommitted. Tests cover acknowledgement,
broker error, timeout and the ordering of DLQ confirmation before source commit.

## Horizontally scalable dashboard API

The production dashboard API can now run multiple replicas without multiplying
the raster-tile allowance or splitting WebSocket status delivery. A
transactional PostgreSQL token bucket stores only SHA-256 client identifiers,
locks one bucket row per request and bounds retained clients. Development keeps
the lightweight in-process implementation; staging and production select the
database backend automatically and refuse an explicit local override. The
blocking database operation is dispatched through Starlette's thread pool.

Every API pod now consumes the status topic through its own stable Kafka group,
derived from the Kubernetes pod name, so every pod receives every status event
for its locally connected WebSockets. A separate shared inbox group still
applies each mission-state mutation exactly once. Duplicate state receipts are
therefore fanned out locally instead of being discarded, and the source offset
is committed only after local broadcast completes.

Migration `0011` creates the shared buckets. The generic production overlay
runs two API replicas with a zero-unavailable rolling update; the cost-focused
OVH preproduction overlay continues to inherit one replica.

## Deferred roadmap

The remaining implementation batches should remain independently reviewable:

1. gradual strict typing of the remaining CPU-visible `gaussian_ortho` boundaries;
2. explicit platform versioning and release policy.
