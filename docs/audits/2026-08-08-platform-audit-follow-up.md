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
| Strict typing for `gaussian_ortho` | Confirmed absent from the current mypy ratchet. | Started with the CPU-visible coverage and filter-policy modules under strict mypy. |
| Pre-approved hashes for dynamic YOLO variants | Confirmed absent; runtime hashes provide provenance but not prior approval. | Separate model-registry batch. |
| Kafka tile-result payload references | Confirmed useful for bounding segmentation messages. | Requires a versioned event/storage migration. |
| Coverage floor above 50% | Confirmed. The full non-GPU/non-integration suite measured 60% branch coverage after both batches. | Raised from 50% to 55% in this batch. |

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
- the full non-GPU/non-integration suite passed: 533 selected tests, with 13
  explicitly deselected;
- branch coverage measured 60%, allowing the enforced floor to move from 50%
  to 55% with five points of headroom;
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
footprints. The coverage and existing filter-policy modules are now part of the
strict-mypy and service-core Ruff ratchets. A fresh Example Quarry GPU E2E is
still required to calibrate these conservative defaults against a complete
post-change product; routine PR CI must not run that long qualification.

## Deferred roadmap

The remaining implementation batches should remain independently reviewable:

1. gradual strict typing of the remaining CPU-visible `gaussian_ortho` boundaries;
2. expected-hash registry for supported dynamic YOLO checkpoints;
3. S3-referenced, hash-verified tile result events;
4. asynchronous Kafka delivery acknowledgements;
5. distributed API rate limiting and WebSocket fan-out before API scale-out;
6. explicit platform versioning and release policy.
