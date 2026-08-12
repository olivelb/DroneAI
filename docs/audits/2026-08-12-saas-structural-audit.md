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
| Control-worker availability | protected overlays run two rolling replicas with PostgreSQL session-lock leadership and a disruption budget | real two-connection exclusion, forced-connection-loss takeover and Helm render tests |
| Health | liveness is process health and readiness executes a real DB query | HTTP probe tests and Helm contract |
| Integration | eight focused tests cross real PostgreSQL, Kafka and MinIO boundaries | isolated Compose integration CI job and migrated temporary database |
| HTTP control plane | a black-box journey bootstraps an organization, uploads to MinIO, launches/cancels a mission and observes published outbox delivery | GPU-free Compose profile with migration, authenticated API and control worker |
| SaaS policy | storage, logical stage concurrency, shared request rate and terminal-mission retention are explicit per-organization controls, separate from science | PostgreSQL policy/RLS, append-only usage ledger, scheduler/upload/middleware/retention tests and operator-only provisioning CLI |
| SaaS isolation | organization is distinct from member identity across auth, DB queries, storage, realtime and quotas | migration round trip plus cross-organization tests |
| Identity control plane | durable members, one-time hashed credentials, transactional rotation/revocation and append-only lifecycle audit | HTTP lifecycle tests plus PostgreSQL migration and immutability checks |
| Identity recovery | organization-admin invitations and member-owned recovery capabilities are hashed, expiring, single-use and transactionally redeemed | HTTP replay/revocation tests plus capability-scoped PostgreSQL RLS |
| Platform support | durable `support` identities can inspect organization metadata and state only, without tenant-admin or data-plane privilege | separate realm tests, SQL update restriction, RLS denial and append-only platform audit |
| Database tenant defense | transaction-local organization context plus PostgreSQL RLS over roots and descendants | real non-owner-role denial tests plus migration round trip |
| Storage control plane | datasets and missions persist organization-scoped prefixes while historical rows remain readable | tenancy helper, migration round trip and upload recovery tests |
| Mission data plane | the durable mission prefix is authoritative across stages, COLMAP/GCP products, tiling, AI results, map fallbacks, frontend browsing, recovery and deletion | real-service composition, tenant/legacy key tests and production source guard |
| Immutable tenant data | tenant workspaces, GCP bundles and detection shards publish under organization-specific CAS identities while historical readers remain available | v3 canonicalization, identical-byte isolation and cross-tenant denial tests |
| Protected compute | staging and production accept only bounded, immutable stage Jobs; the API, control worker, Helm overlays and compatibility workers all fail closed against fused execution | deployment-mode tests, protected Helm renders and worker startup guards |
| Tenant event plane | organization binds every new pipeline/status/control event, deterministic identity, trace correlation, Kafka key, cancellation cache and realtime audience check | cross-organization identity, routing, cancellation and fan-out tests plus exported JSON Schema |
| Physical compute accounting | organization/mission concurrency counts logical runs while global/resource budgets count physical units; detection finalization is CPU-only | two-tenant/four-shard/two-GPU scheduler test, CPU manifest and rolling-upgrade recreation tests |
| Frontend structure | HTTP transport and multipart upload are isolated from domain API calls; every JSON endpoint and websocket status event now requires a domain runtime decoder | 32 Vitest tests, ESLint, explicit TypeScript gate, production build and 10 browser journeys |

The qualified local baseline after these changes is 983 non-GPU/non-integration
Python tests, eight focused real-service integration tests, one real HTTP
control-plane journey, 32 frontend unit tests and 10 Playwright journeys. The
full Python static gate, documentation links, schema sync, shellcheck and
actionlint also pass.

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
the audited adoption migration exists. The sixth phase now separates logical
stage-run concurrency from physical pod units, caps indexed detection
parallelism against actual scheduler capacity and re-enters the
receipt-verified finalizer through the CPU scheduler. The shared SaaS data-plane
P0 sequence is complete; the control-plane and operability work below remains
deliberately separate from scientific qualification.

### P1 — SaaS control plane

1. **Identity federation.** Durable memberships, hashed credentials,
   rotation/revocation, one-time invitations, self-issued recovery and the
   separate metadata-only support realm are implemented without widening the
   organization `admin` role. OIDC remains pending until a concrete provider,
   issuer/audience/JWKS policy and claims/account-linking contract are selected.

### P2 — Maintainability and operability

1. **Split remaining hotspots by ownership boundary.** Priority files are
   `shared/database.py` (models by bounded context),
   `shared/pipeline_params.py` (contract/catalogue),
   `app4-dashboard/api/dataset_uploads.py` (commands, S3 gateway, recovery),
   `app3-processing/analysis_workflow.py` (campaign, aggregation, publication),
   `shared/storage.py` (client, CAS, multipart) and
   `app4-dashboard/api/routers/map_gcps.py` (read/write/audit routes).
2. **Observability and SLOs.** Export request, outbox age, scheduler queue,
   reconciliation, Kafka lag, S3 failure and organization-usage metrics with
   trace correlation and alert thresholds.
3. **Fault and concurrency qualification.** Exercise killed API/control
   processes, duplicate Kafka delivery, stale leases, S3 timeouts, concurrent
   upload finalization and rolling migration compatibility.
4. **Data migration tooling.** Provide dry-run, resumable, audited adoption of
   legacy storage into organizations and eventually make mission identifiers
   unique within an organization rather than globally.

## Scientific qualification remains separate

Dataset-backed E2E and benchmarks continue to answer scientific questions:
accuracy, completeness, reconstruction stability, raster quality, GCP/RTK
behavior, inference quality and GPU capacity. A platform defect may invalidate
a run operationally, but a scientific result must not block fixes to tenancy,
auth, CI, migrations, probes, modularity or recovery that are fully testable
with synthetic data.
