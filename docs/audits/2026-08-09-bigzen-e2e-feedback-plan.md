# BIGZEN E2E feedback implementation plan — 2026-08-09

## Objective

Turn the observations from the Villesèque P4 E2E into independently
deliverable changes. The target model is a multi-tenant platform where a
mission owns immutable, reusable phase results rather than one mutable linear
workspace.

## Findings grouped by concern

### Runtime truth and observability

- A service-level snapshot cannot describe reconstruction and DroneGS as two
  independent phases when both are emitted by the COLMAP worker.
- Silence beyond the heartbeat threshold was presented as a pipeline error,
  even though it is an observability warning and not evidence of failure.
- A recovered service could leave the persisted mission in a terminal error.
- Summary polling replaced WebSocket console history with an empty log list.
- Progress needs stable phase names, timestamps, attempts, counters and regular
  heartbeats rather than inference from UI navigation.

### Mission and artifact lifecycle

- Launching a mission, monitoring existing missions and exploring their
  products are separate workflows.
- A mission must retain its owner, submitted parameters and multiple attempts.
- Reconstruction, Gaussian training, filtering, raster publication and AI
  analysis must be independently runnable when their declared inputs exist.
- Ortho/DSM, filtered point clouds and AI campaigns need immutable identities
  and explicit parent links so a downstream run selects one exact upstream
  artifact.

### Product configuration

- User-facing defaults should be `Fast`, `Normal` and `High Quality`; pipeline
  implementation names such as `modern` and `legacy` are not quality profiles.
- YOLO class choices must come from the selected model manifest, not from a
  short frontend constant.
- A mission request must explicitly select the phases to execute.

### Explorer

- Polling must not re-enable a detection layer hidden by the operator.
- AI features need audited correction/deletion, bulk operations and vector
  export.
- Raster styling needs RGB/band selection, stretch controls and useful DEM
  palettes while keeping QGIS export as the advanced escape hatch.
- DSM tiles must share one display range; per-tile percentile normalization
  creates false elevation discontinuities.

### Cloud scheduling

- Phase jobs need declared CPU, RAM, GPU and VRAM requirements.
- The scheduler may run missions or independent phases concurrently only when
  dependency and resource constraints are satisfied.
- The final Kubernetes implementation should create bounded Jobs/pods per
  phase request instead of keeping expensive workers permanently allocated.

## Delivery phases

### Phase 1 — E2E correctness fixes

Acceptance criteria:

- reconstruction is complete while DroneGS is running;
- a stale heartbeat is shown as delayed updates, never as a computed failure;
- retry progress clears a recovered error without resurrecting cancelled or
  successful missions;
- polling cannot clear the live console or re-enable hidden analysis layers;
- every DSM tile maps a given elevation through the same global display range.

### Phase 2 — Internationalization foundation

- Add a small typed translation catalog and locale provider without a heavy
  framework dependency.
- Make English the default and add French as the second catalog.
- Migrate the shell, mission monitor, launch flow and explorer incrementally;
  untranslated keys must fail tests rather than silently mixing languages.

### Phase 3 — Profiles and model capabilities

- Define versioned `fast`, `normal` and `high-quality` parameter profiles in
  the shared backend contract, including an explicit maximum Gaussian count.
- Return profile metadata and effective values through the parameters API.
- Expose installed YOLO model manifests and their complete class maps.
- Persist selected profile, model identity and effective overrides per run.

### Phase 4 — Mission catalogue and detailed monitor

Status: implemented on 2026-08-09. Migration `0013` persists the authenticated
owner, the API and WebSocket streams enforce that boundary, and the frontend
separates launch, paginated catalogue and mission detail routes. The current
attempt list is an explicit compatibility projection until Phase 5 introduces
immutable stage-run records.

- Split the launch page from a paginated, owner-scoped mission catalogue.
- Add a dedicated mission detail route with parameters, attempts, phase state,
  heartbeat age, logs and products.
- Enforce owner scope in every mission, raster, vector and analysis query;
  administrative cross-tenant access remains explicit and audited.

### Phase 5 — Versioned phase DAG

Status: control-plane contract implemented on 2026-08-09. Migration `0014`
adds per-stage attempts, immutable artifacts and exact parent edges. Mission
commands select a dependency-closed phase set; owner-scoped retry/publication
routes use deterministic idempotency and exact upstream artifact identities.
Legacy worker events are projected during the incremental executor migration.
Bounded per-stage Kubernetes execution remains Phase 7.

- Introduce `mission_stage_runs`, immutable `mission_artifacts` and parent-edge
  records through an additive migration.
- Record queued/running/terminal state, attempt, executor, parameters,
  provenance, quality metrics and artifact checksums per phase run.
- Accept a selected phase set and exact upstream artifact IDs in commands.
- Make reconstruction, training, filtering, rasterization and detection
  independently retryable and idempotent.
- Preserve existing missions through a compatibility projection while the
  workers migrate one phase at a time.

### Phase 6 — Explorer editing and raster styles

Status: implemented on 2026-08-09. Migration `0015` adds reversible feature
tombstones, reviewed state, append-only before/after audit events and named
raster display recipes. The owner-scoped API and dashboard support bulk
correction of manual/persisted-AI features, global percentile or fixed band
stretches, RGB composition and compact DEM palettes. Vector exports omit
tombstones while preserving the rows for restoration and audit.

- Use feature tombstones and an audit event for single/bulk correction instead
  of irrecoverable deletion.
- Add reviewed/unreviewed state, bulk selection and GeoJSON/GPKG export.
- Add band selection, RGB composition, opacity, min/max or percentile stretch
  and a compact set of DEM palettes.
- Persist named layer styles separately from immutable raster artifacts.

### Phase 7 — Resource-aware orchestration

Status: control plane and all five worker adapters implemented on 2026-08-09;
GPU qualification and deployment activation remain. Revision `0016` and the
v1 DAG catalogue persist portable CPU/GPU/VRAM resource classes. A pure round-robin scheduler
enforces global, owner, mission and resource-class concurrency, and a hardened
Kubernetes Job renderer plus least-privilege opt-in RBAC is covered by tests.
Transactional reservation, idempotent creation/recreation, bounded dispatch,
cancellation, heartbeat and reconciliation are implemented. Job mode
intentionally remains disabled until the fused workers are split into qualified
one-shot stage executors. The shared claim/heartbeat/cancellation/artifact
boundary and verified S3 workspace hand-off are implemented and tested;
the COLMAP reconstruction adapter and portable reconstruction state are also
implemented. Gaussian training, filtering and rasterization now run as
independent bounded commands with checksum-bound JSON/PLY handoffs, immutable
parent handling, and one shared GeoTIFF/coverage finalizer. Detection now
restores that raster artifact, streams bounded overlapping tiles through YOLO
or SAM3, validates stable model provenance, deduplicates overlaps and publishes
raw JSON plus WGS84 GeoJSON. Job mode stays disabled until a representative GPU
E2E qualifies the complete chain and deployment configuration provides all
immutable executor images.

- Add resource-class declarations to phase definitions.
- Queue ready DAG nodes and enforce per-owner/global concurrency limits.
- Implement Kubernetes Job launch, cancellation, heartbeat and reconciliation.
- Allow same-mission parallelism only for independent nodes and record the
  selected executor/GPU architecture in provenance.

### Phase 8 — Qualification and operations

- Add migration, authorization, event-ordering, retry and concurrency tests.
- Exercise multiple missions, multiple products and multiple AI runs in E2E.
- Re-run Villesèque with the corrected monitor and DSM style contract.
- Document backup, retention, cost controls, failure recovery and operational
  dashboards.

## Confirmed initial defaults

1. Start with English and French only.
2. Treat feature removal as an audited soft deletion/tombstone; administrators
   may purge data only through a separate retention operation.
3. Treat mission products as immutable and create a new phase run for every
   recalculation; never overwrite the selected parent artifact.
4. Use these initial profile envelopes, then benchmark them before declaring
   them stable: Fast (1,600 px / 2,048 features / 7,500 iterations / 1.5M
   Gaussians, exactly 1,500,000), Normal (2,400 px / 4,096 features / 15,000
   iterations / 3M Gaussians, exactly 3,000,000), High Quality (4,096 px /
   16,384 features / 30,000 iterations / 5M Gaussians, exactly 5,000,000).
5. Use the authenticated principal subject as the first owner boundary, with
   organization/project tenancy added without changing artifact identities.
