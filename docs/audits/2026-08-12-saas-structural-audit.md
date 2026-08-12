# SaaS structural audit and hardening — 2026-08-12

## Decision boundary

This audit evaluates software structure, isolation, reliability, deployment,
tests and maintainability. It deliberately does not reinterpret BIGZEN results
or change reconstruction, Gaussian, raster, GCP or detector thresholds. CUDA
12.9 remains the validated runtime and Node 20 remains the supported frontend
runtime.

Until OVH CPU/GPU test pods are available, the existing browser E2E remains a
useful operator-journey metric. It is complemented here by a real synthetic
Postgres/Kafka/MinIO composition test; neither is presented as scientific
dataset qualification.

## Implemented in this hardening series

| Area | Delivered invariant | Verification |
|---|---|---|
| CI closure | Python duplication and the IA container can no longer be skipped by an affected change; merge queues execute the full gate | path-classifier tests, actionlint, container matrix |
| Input boundaries | mission/run identifiers are constrained before becoming local paths or object keys | event, tile and workspace tests; exported JSON Schema |
| API lifecycle | durable outbox, upload reconciliation and stage scheduling run in a dedicated control worker | lifecycle tests, Compose config, Helm render and least-privilege RBAC |
| Health | liveness is process health and readiness executes a real DB query | HTTP probe tests and Helm contract |
| Integration | one test crosses a real Postgres outbox transaction, Kafka delivery/consumption and verified MinIO round trip | isolated Compose integration CI job |
| SaaS isolation | organization is distinct from member identity across auth, DB queries, storage, realtime and quotas | migration round trip plus cross-organization tests |
| Identity control plane | durable members, one-time hashed credentials, transactional rotation/revocation and append-only lifecycle audit | HTTP lifecycle tests plus PostgreSQL migration and immutability checks |
| Database tenant defense | transaction-local organization context plus PostgreSQL RLS over roots and descendants | real non-owner-role denial tests plus migration round trip |
| Storage control plane | datasets and missions persist organization-scoped prefixes while historical rows remain readable | tenancy helper, migration round trip and upload recovery tests |
| Mission data plane | the durable mission prefix is authoritative across stages, COLMAP/GCP products, tiling, AI results, map fallbacks, frontend browsing, recovery and deletion | real-service composition, tenant/legacy key tests and production source guard |
| Immutable tenant data | tenant workspaces, GCP bundles and detection shards publish under organization-specific CAS identities while historical readers remain available | v3 canonicalization, identical-byte isolation and cross-tenant denial tests |
| Protected compute | staging and production accept only bounded, immutable stage Jobs; the API, control worker, Helm overlays and compatibility workers all fail closed against fused execution | deployment-mode tests, protected Helm renders and worker startup guards |
| Tenant event plane | organization binds every new pipeline/status/control event, deterministic identity, trace correlation, Kafka key, cancellation cache and realtime audience check | cross-organization identity, routing, cancellation and fan-out tests plus exported JSON Schema |
| Frontend structure | HTTP transport and multipart upload are isolated from domain API calls; auth responses are runtime-validated | 29 Vitest tests, ESLint, explicit TypeScript gate and build |

The qualified local baseline after these changes is 940 non-GPU/non-integration
Python tests, five real-service integration tests, 29 frontend unit tests and 10
Playwright journeys. The full Python static gate, documentation links, schema
sync, shellcheck and actionlint also pass.

## Main remaining defects that do not require scientific datasets

### P0 — shared SaaS data plane

The follow-up review at `f295d4f521caa0924a65740be9a5572bf75cb0c7`
corrected an earlier overstatement: persisting `Mission.workspace_prefix` did
not migrate every producer. At that revision, the bounded and fused compute
paths still contained literal `missions/{vol_id}` keys, and Manifest v2 still
writes global
`blobs/sha256` keys.

The first corrective phase binds every bounded Job to its durable
organization, mission and workspace prefix, moves it to a non-owner
`stage-database-url`, applies the organization to every executor transaction,
and verifies cross-tenant denial with the real PostgreSQL role. The second
phase makes `Mission.workspace_prefix` non-nullable and authoritative in every
mission-object producer and consumer, including event propagation, recovery
and exact-prefix deletion. The third phase isolates new tenant workspaces, GCP
bundles and detection shards in organization-specific CAS while retaining
historical v1/v2 reads. The fourth phase rejects fused compute in protected
environments at both the application and Helm boundaries. The fifth phase
binds organization to all new pipeline, status and control events, their
deterministic IDs, trace correlation, Kafka routing, cancellation and realtime
audience validation. Historical version-one events without an organization
remain readable; mission identifiers deliberately remain globally unique until
the audited adoption migration exists. The remaining ordered phase is to
account physical fan-out resource units and run the detection finalizer on CPU.

### P1 — SaaS control plane

1. **Identity federation and platform support.** Durable memberships, hashed
   credentials and rotation/revocation are implemented. Add invitations, OIDC
   federation, recovery flows and a distinct platform-support role without
   widening the organization `admin` role.
2. **Real HTTP black-box composition.** The browser suite mocks the API and the
   new integration suite exercises service clients below HTTP. Add a small
   Compose profile running migrations, API and control worker against real
   services, then launch/cancel a synthetic mission without GPU work.
3. **Control-worker availability.** The first deployment is a non-overlapping
   singleton (`Recreate`). Add leader election or qualify all reconciliation
   loops for active-active replicas before claiming control-plane HA.
4. **Quota and retention ledger.** Enforce organization storage, concurrent-job,
   request and retention policies with auditable usage records. Do not reuse
   scientific quality profiles as commercial quotas.

### P2 — Maintainability and operability

1. **Split remaining hotspots by ownership boundary.** Priority files are
   `shared/database.py` (models by bounded context),
   `shared/pipeline_params.py` (contract/catalogue),
   `app4-dashboard/api/dataset_uploads.py` (commands, S3 gateway, recovery),
   `app3-processing/analysis_workflow.py` (campaign, aggregation, publication),
   `shared/storage.py` (client, CAS, multipart) and
   `app4-dashboard/api/routers/map_gcps.py` (read/write/audit routes).
2. **Complete runtime response validation.** Authentication now fails closed,
   but other frontend endpoints still trust the generic JSON transport after
   compile-time typing. Add generated or lightweight decoders for mission,
   map, GCP, upload and pod responses.
3. **Observability and SLOs.** Export request, outbox age, scheduler queue,
   reconciliation, Kafka lag, S3 failure and organization-usage metrics with
   trace correlation and alert thresholds.
4. **Fault and concurrency qualification.** Exercise killed API/control
   processes, duplicate Kafka delivery, stale leases, S3 timeouts, concurrent
   upload finalization and rolling migration compatibility.
5. **Data migration tooling.** Provide dry-run, resumable, audited adoption of
   legacy storage into organizations and eventually make mission identifiers
   unique within an organization rather than globally.

## Scientific qualification remains separate

Dataset-backed E2E and benchmarks continue to answer scientific questions:
accuracy, completeness, reconstruction stability, raster quality, GCP/RTK
behavior, inference quality and GPU capacity. A platform defect may invalidate
a run operationally, but a scientific result must not block fixes to tenancy,
auth, CI, migrations, probes, modularity or recovery that are fully testable
with synthetic data.
