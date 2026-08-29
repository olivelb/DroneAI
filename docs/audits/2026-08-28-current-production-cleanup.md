# Current-production cleanup — 2026-08-28

Status: **source cleanup and general review complete; delivery checks tracked in the final section**.
Base: `21090f2`; branch: `codex/current-production-cleanup`.
Authoritative checkout: `/home/olivier/droneAI` in Ubuntu WSL2.
The initial worktree was clean. The sections below record successive verification
lots; the final review section contains the current delivery status. No live
mission data or production deployment is changed by these checks.

## Scope and decisions

The operator requested current production paths only, without old-run replay,
experimental variants or rollback implementations. Fast v2 and HQ v4 were
explicitly confirmed qualified and required. Normal v3 remains the existing
default. Current facade training retains `DRONEGS_FACADE_HD_V3`.

The operator also authorized removal of the historical renderer and aggregation,
then the remaining renderer diagnostics. Image tests must use hardware WebGPU,
with Windows Chrome when hardware WebGPU is unavailable in WSL.
The operator then selected **organization-scoped Artifact Manifest v3 only**.
Directional opacity SH must remain available; native training, export, decode
and viewer support have been preserved.

The operator subsequently confirmed that all new missions will publish versioned
artifacts and that historical missions will be removed separately. This
authorizes 404/empty map layers when an immutable raster/detection artifact is
absent, even if old SQL rows or root-level files still exist. It does not
authorize this cleanup to delete mission data.

No source imagery, published run outputs, database rows, external qualification
workspaces, Docker images or third-party libraries have been removed. Historical
database migrations and durable audit action names remain intact.

## Implemented and verified

| Area | Change | Verification |
|---|---|---|
| Quality profiles | Keep Fast v2, Normal v3 and HQ v4; reject retired IDs. Remove replay registry, candidate gate and former facade identities. | API, worker, local-runner tests and regenerated event schemas. |
| Qualified initialization | Preserve protection against changing qualified initialization via expert overrides. | Negative override tests remain. |
| Reconstruction preset | Keep only modern in API, event schema, worker merge and frontend. Reject legacy/unknown modes instead of silently substituting modern. Remove UI selector and state-reset callback. | All 125 modern parameters unchanged; 119 focused Python tests and frontend rejection test. |
| Local runner | Fast uses v2; remove experimental fast-resident preset. | Existing runner tests. |
| Historical storage adoption | Remove isolated adoption CLI, planner, executor, adapter and their tests/docs/CI selectors. | No live imports; migrations and audit vocabulary unchanged. |
| Workspace storage | Keep only Artifact Manifest v3, mandatory tenant CAS and one publisher. Remove v1/v2 readers, global publisher branches, legacy restore wrapper and writer rollout flag. | Canonical byte parity, CPU checks, real local MinIO round trips, full/selective restore, parent and tenant rejection. |
| CAS consumers | Remove GCP v1 replay/global publication and global shard receipt finalization. Keep the current tenant-bound GCP schema v2. | GCP/receipt/security tests; existing quality, coordinate and checksum checks retained. |
| Helm/control | All workspace writes use v3. Remove only the old writer-version flag; preserve detection selective/fan-out and RLS gates. | Base/prod/preprod templates render; invalid fan-out and RLS configurations still fail. |
| CRS aliases | Remove unused read_saved_utm_crs/save_utm_crs aliases. | Projected-CRS implementation unchanged. |
| GSTile producer | Keep adaptive-moment V4 and depth-spatial aggregate packs. Remove minhash, stratified, V3, leaf-only/individual-pack writers and historical repacker. | All 11 bundle files byte-identical to the base implementation; producer tests. |
| GSTile benchmark | Use current shared production defaults; remove discovery/adaptation to historical CLI flags. | Real synthetic CLI smoke run. |
| Detection aggregation | Remove legacy_aggregation, unversioned dispatcher path and obsolete journal tests. Move shared dedupe unchanged to processing_core. | Current cancellation, versioned analysis and recovery tests retained; 49 focused tests. |
| GSTile readers | Accept adaptive V4 only in Python and TypeScript. | Retired-profile rejection and integrity/bounds/coverage tests. |
| GSTile renderer | Remove standalone/non-LOD, reference PLY, tiled and incremental rendering plus old transition planners. Retain merged arena commits and worker/main-thread decoding. | Frontend tests, source comparison at structural checkpoint and hardware image checks. |
| Renderer diagnostics | Remove CPU sort and float32 resource alternatives. Remove opacity/radial selectors; use directional opacity mode 1 and GPU sort. Public scale/coverage/sibling URL selectors removed. | Qualified shader sources unchanged; hardware rendering with old query parameters still passes. |
| RAM cache | Fixed lazy 1,536 MiB bounded SLRU; remove standard/desktop query profiles. | Lazy-allocation and bounded-revisit checks retained. |
| Documentation | Update current Q96/V4 policy, developer hardware-test procedure and active entry points. Historical mixed implementation reference is marked non-operational. | Markdown link check. |

The modern parameter parity count and equality are recorded in
`modern-defaults-parity.json`; the full before snapshot is retained locally.
Fast/HQ numeric defaults were not changed.

### V4 producer parity

Fixture: 8,193 synthetic Gaussians; leaves/chunks 2,048; proxies 1,024; remaining
settings use current production defaults. The base producer was loaded from Git
and compared against the cleaned implementation. All 11 files, including the
manifest and compressed packs, are byte-identical:

`sha256:dbb8e3f7748c5cf93983cb413bcd31a2ccb1a54de912073e175b9debce5bc3ac`

The comparison was repeated after reader cleanup. Evidence is retained in
`tmp/cleanup-20260828/`, excluded from Git.

## Hardware WebGPU verification

Chrome 151 on Windows uses an NVIDIA Lovelace adapter with
`isFallbackAdapter=false`. The Playwright harness runs through Windows Node
against the authoritative WSL source, a WSL Next server and a real range server
reading the parity fixture. Chrome uses a temporary profile.

The harness rejects software/fallback adapters before scene loading and checks
actual scene pixels below the HUD. It supports worker and main-thread assembly,
and exercises obsolete query parameters without restoring retired renderers.
A retired manifest must fail before any pack is requested.

The isolated baseline frontend was built from `21090f2`, using the same installed
dependencies and a temporary Turbopack root setting. Its application sources
were unchanged. The same current harness tested baseline and cleaned builds.

Initial-scene comparison, excluding HUD and border:

- crop: x=16..1394, y=160..615, covering 626,990 pixels;
- before/after mean absolute RGB error: **0.001483 / 255**;
- largest channel difference: **10 / 255**;
- differing pixels: **1,848 (0.295%)**;
- same-build repetitions and worker/main-thread/retired-query captures show
  mean errors of **0.000432 to 0.007615 / 255**, maximum channel difference 21.

The before/after difference lies within the observed same-build variability.
This is **not exact pixel parity**, a causal diagnosis of the variability, or
a new scientific qualification. The small fixture fully fits the initial
resident budget: large-scene LOD replacement, seams and performance remain
outside this check.

Earlier SwiftShader runs exposed a blank-scene false positive in the old
HUD-only smoke assertion. Those results are superseded for image acceptance;
no software-rendered image qualifies this cleanup.

## Earlier renderer verification

- CPU: **1,601 passed, 1 skipped, 36 deselected** using
  `pytest -m "not gpu and not integration"`. The optional missing `plyfile`
  dependency was also absent in the baseline.
- `make static PYTHON=.venv/bin/python`: passed (compile, Ruff, strict mypy,
  shellcheck, Markdown links, evidence/schema/version checks, actionlint).
- Frontend: **377 unit tests in 42 files**, ESLint, TypeScript and production
  build passed.
- Standard Chromium browser suite: **10 passed**, with mocked API routes.
- Final build, hardware Chrome/WebGPU NVIDIA: **4 passed** (worker assembly,
  main-thread fallback, retired-query parameters and retired-profile rejection).
- Baseline hardware capture: **1 passed**. Three same-build hardware repetitions:
  **3 passed**.
- Synthetic producer/benchmark parity and modern parameter parity: passed.
- No native/CUDA rebuild, external-service integration, full real mission or
  large-scene scientific qualification is claimed.

Evidence logs include `python-hardware-final.log`, `static-hardware-final.log`,
`frontend-modern-final.log`, `browser-modern-final.log`,
`windows-chrome-baseline.log`, `windows-chrome-current.log`,
`windows-chrome-repeat.log`, `windows-chrome-final.log`,
`hardware-image-parity.json`, `reader-parity.json` and
`modern-defaults-parity.json`.

`viewer-source-parity.json` describes the earlier structural checkpoint:
merged commit operations differed only by the redundant plan guard, while
resource creation/fetch/decode were then unchanged. The later removal of
float32 resource branches means that resource creation is no longer claimed
source-identical. The opacity shader file is still unchanged.

## Remaining work — not claimed complete

| Area | Finding and next gate |
|---|---|
| Historical namespace metadata | Workspace/global CAS compatibility is removed. Historical mission-root helpers, database migrations and audit identities remain; trace their non-artifact consumers separately before removing them. No old objects or database rows have been altered. |
| Map products and vectors | Resolved after explicit operator confirmation: artifact-only raster/pipeline detections, source=pipeline, metadata-driven layer discovery and versioned raw analysis tiles. See the cartographic verification below. |
| Deployment topology | Resolved later in this audit: standalone analysis creation/retry now queues bounded Stage Jobs through analysis_support.py and shared/analysis_stages.py. The earlier Kafka-only observation described the intermediate state, not the final PR #292 tree. |
| Native internals | Only reference-absolute/FastGS/spatial-bounds are public; old local presets and V4 checkpoint replay are removed. Internal bounded raster oracles and dormant AbsGrad/AA buffers remain; do not remove current numerical test oracles merely because they are not the production renderer. |
| Internal renderer controls | Scale validation and coverage defaults remain. Execution approval rejected broader guard removal and direct opacity shader rewriting; safer selector-only removals were applied. Dormant diagnostic shader branches remain in the unchanged qualified shader source. |
| Historical reports | Most dated native/scientific benchmark reports remain historical evidence. Remove reports only after their associated implementation is finally retired; they are not new release acceptance. |

The requested full cleanup is **not finished**. Storage, public native paths,
events and map fallbacks have been cleaned and checked. Historical namespace
helpers and distributed execution dependencies still require closure.
No deployment or data migration should be inferred from this cleanup.


## V3-only storage lot — final verification

The explicit operator choice removes artifact replay compatibility rather than
migrating old data. The public workspace publisher is now `publish_workspace`
and always writes v3; CAS publication and restoration require a real
organization. Old reader constants, canonical v2 writer, v1 file-count wrapper,
GCP replay allowances, global receipt fallbacks and the v2 rollout flag have
been removed. The detection-only selective-restore switch remains independent
and off by default. The qualified scientific/rendering source is unchanged in
this lot.

- CPU: **1,611 passed, 1 skipped, 38 deselected**. The skip remains optional
  `plyfile`; two new real-S3 tests account for the additional integration
  deselections.
- Focused storage/adapter/GCP/shard gate: **167 passed** before seven additional
  organization/parent safety cases; the final full suite includes them.
- Local real MinIO: **3 passed**. Existing signed-part-length test plus v3
  parent/child publication, same-content tenant isolation, full/selective
  restore, mismatching tenant/checksum rejection, and forced conditional
  multipart/reuse/cleanup were exercised.
- MinIO used a new loopback-only container from the already installed image
  with temporary in-memory data and test-only credentials. No deployed object
  store or production data was used.
- Canonical v3 before/after bytes: **identical**, 501 bytes,
  SHA-256 `b330fdc880d640de2116539cebf0a55265c81353a846a22301ed56165457161e`.
- Full static gates passed. Helm lint and default/production/preproduction
  rendering passed. Negative fan-out-without-selective-restore and
  production-without-RLS configurations are still rejected.
- No actual OVH multipart probe, full distributed stage chain, native/CUDA
  rebuild or deployment is claimed. Earlier hardware WebGPU results remain
  the viewer evidence; no image rendering changed in this lot.

Evidence in `tmp/cleanup-20260828/`: `storage-v3-cpu-final.log`,
`storage-v3-static-final.log`, `storage-v3-focused.log`,
`storage-v3-safety.log`, `storage-v3-minio.log`,
`storage-v3-helm-checks.json`, `artifact-v3-parity.json` and rendered YAML.
The initial suite failures were obsolete fixture/caller contracts and were
fixed without weakening production checksum, parent or tenant validation.

Release requirement: drain old work and deploy matching API/control/executor
images. New code rejects old workspace and global GCP/shard inputs; a provider
check and a fresh stage campaign are still required. Old objects remain intact.


## Native optimizer lot — final verification

The versioned registry now contains only `reference-absolute`. The 26 retired
optimizer definitions, their learning-rate branches, CLI help entries,
manifest alternatives, dedicated ablation tests and the obsolete
`compare_gaussian_qualification_runs.py` tool were removed. The current
optimizer retains numeric enum identity 1 and exactly the same learning-rate
arithmetic and constants. Native standalone optimizer defaults now select it.
The shared catalog exposes only this optimizer.

Directional **opacity SH remains supported**, including the existing
`--opacity-sh 0|1` option, moments, progressive SH, PLY fields, GSTile decoding
and viewer shader. The qualified shader source remains unchanged. The
cleanup does not change the opacity-SH mathematical kernels.

Verification uses the installed development image
`dronegs-dev:pixel-weighted-6865308` (CUDA 12.9.86, CMake 3.28.3), the
authoritative source mounted read-only, and an NVIDIA RTX 4070 Laptop.
Baseline and cleaned builds each pass **8/8 CTest suites**. Shared GPU tests
formerly named dev16/dev38 were retained and moved to the current optimizer,
including FastGS gradients, opacity-SH memory/updates, topology and checkpoint
resume. Two fixture adaptations preserve the existing assertions:

- the positional-noise test now uses the existing eight-Gaussian population;
  two samples yield coincident floor percentiles and a zero production rate;
- the convergence fixture uses FastGS and 150 steps, below the first topology
  window, instead of the old optimizer's 30 steps; the required 5% loss
  reduction, parameter-update and normalization thresholds are unchanged.

A separate local probe runs 64 steps with color SH3, opacity SH enabled,
FastGS, fixed seed and one spatial topology refinement for schedule budgets
7,500 / 15,000 / 30,000. Four baseline runs and two cleaned runs have identical
learning-rate traces and all grow from 8 to 9 Gaussians. The greatest
before/after parameter difference is **2.384185791015625e-7**; the greatest
same-build difference is also that value. Mean absolute differences are at
most 2.861e-9. At the 7,500 schedule, the cross-build maximum exceeds the
sampled baseline-only maximum but equals the cleaned-repeat maximum. This is
a small-fixture numerical regression check, not exact bit parity or a full
scientific qualification campaign.

CPU checks: **1,608 passed, 1 skipped, 38 deselected** (three removed tests
were specific to retired AbsGrad optimizer comparisons). Full static gates
passed. Existing performance/native-crop qualification tools and tests remain
because they still exercise current code.

Evidence: `native-baseline.log`, `native-final.log`,
`native-benchmarks-final.log`, `native-parity.json`,
`native-cleanup-cpu.log`, `native-cleanup-static.log` and the local probe
sources/results under `tmp/cleanup-20260828/`. Initial fixture failures are
retained in the intermediate logs; no production math was changed to make
them pass.

The initial grouped public-selector/preset removal was rejected before
execution. A materially narrower runner-only alternative was then accepted:
remove smoke/low-memory, retain the common balanced values as internal
defaults, and preserve each current initializer. All fields of Fast v2,
Normal v3, HQ v4 and facade v3 compare identical before/after. The standalone
runner now defaults to Normal v3. Separate boundary changes restrict Python
and native CLI choices to reference-absolute/FastGS/spatial-bounds and remove
the obsolete auto-raster helper. Negative tests cover retired selections.

Checkpoint reading now accepts V5 only. Checksum, size, identity, capacity,
optimizer and SH state validation remain; V4 rejection was added to the
existing resume test. Native tests and the retained benchmark targets
compile, and all **8/8 CTest suites pass** after this final change.
No full-cleanup completion, deployment or requalification is claimed.

Additional evidence: `local-qualified-before.json`,
`local-qualified-parity.json`, `native-selectors-build.log`,
`native-checkpoint-final.log`, `native-source-parity.json` and
`native-manifest-parity.log`. All 28 CUDA kernels in rasterization.cu are
source-identical to the base. The qualified opacity-SH-enabled manifest
compares byte-identical: 6,913 bytes, SHA-256
`8811a3ef508b9bf6b06213298f72f7c5f70b4bbc8dee61490c15d0a1f23e77fc`.

## Event envelope lot

Consumers no longer infer missing event type/version, synthesize an event ID,
or fill trace metadata for unversioned payloads. The producer-side make_event
constructor still provides the complete current envelope. Schema version must
be an integer, and attempt is mandatory in the published JSON schema.
Malformed/old envelopes use the existing dead-letter handling.

The full CPU suite passed **1,623 tests**, with the same optional skip and
integration/GPU deselections, after adapting two old payload fixtures.
Ten new rejection cases cover missing envelope fields and non-integer version
values. Existing retry, deferred-message, commit and dead-letter tests remain.

Deployment must drain old incomplete messages and use matching producers and
consumers. No Kafka offset, deployed queue or persisted message was mutated.
Final checks are recorded in `cleanup-cpu-final.log` and
`cleanup-static-final.log`.

## Cartographic cleanup — operator gate resolved and verified

An earlier grouped removal was rejected before execution; none of that attempt
was applied. The operator then explicitly confirmed that all future missions
have versioned artifacts and that old missions will be discarded separately.
The changes below were subsequently applied and tested:

- Raster metadata, tiles and exports resolve only the latest
  raster_product_workspace through the tenant-bound v3 manifest. Missing
  artifacts return 404 without probing a root object.
- Map/search/export use the current immutable detection_workspace. Missing
  detection artifacts yield an empty pipeline layer, never a SQL Detection
  fallback.
- The current artifact layer is called pipeline in API source/scope selectors
  and the frontend. Retired legacy selectors are rejected. The immutable
  pipeline feature reader, including its identity/structure/bounds/limit
  checks, changed only its source label.
- Manual MapFeature annotations and AI analysis features, run filters and
  export scope=all remain supported. The SQL Detection spatial filter and
  historical map-key helper were removed; mission/layer validation remains.
- Layer availability in the frontend comes from the latest raster artifact's
  ortho_file/height_file metadata, no longer from a mission-root browse. Mission
  detail orders artifacts deterministically by creation time and database ID.
  The browser test verifies that the elevation button remains enabled with
  only versioned raster metadata.
- Non-persisted analyses accept the current TileResultArtifact schema v1, not
  legacy per-tile FeatureCollections. This schema version is independent of
  Artifact Manifest v3. Existing conversion/bounds/limits are retained;
  malformed, retired and wrong-mission payloads are rejected with 502.
- COG inspection when a sidecar is absent, scalar display-range controls and
  NaN normalization remain unchanged. No qualified raster or opacity shader
  math was modified.

Verification on the resulting source:

- CPU: **1,632 passed, 1 skipped, 38 deselected**. The skip remains the optional
  plyfile dependency; GPU/integration cases are not counted as CPU executions.
- Focused analysis/map API tests: **56 passed**.
- Frontend: **380 tests in 42 files**, ESLint, TypeScript and production build
  passed.
- Chrome Windows: **14 passed**, comprising ten operator journeys and four
  GSTile cases. The hardware tests report NVIDIA Lovelace and
  isFallbackAdapter=false, and retain actual rendered-scene pixel checks.
- Full make static and git diff --check passed.

The first browser run after updating the mission fixture had one stale catalogue
assertion expecting kind=orthomosaic; the fixture now supplies
kind=raster_product_workspace. The assertion was corrected to the current
contract, and all 14 tests passed on the next complete run. The map/export,
elevation discovery and four GPU cases already passed in that intermediate run.

Evidence: map-cpu-final.log, map-static-final.log, map-analysis-reader.log,
map-discovery-frontend.log and map-browser-final.log, under
tmp/cleanup-20260828/. Intermediate map-discovery-windows-chrome.log retains
the failed historical-label assertion. No new real mission, PostGIS integration
run, deployed Kafka run or full scientific campaign is claimed.


## Standalone analyses — migrated to bounded Stage Jobs

The user authorized preserving map-triggered analyses by migrating them before
removing the IA/processing Kafka workers. The following changes are applied:

- Migration 0037 links an independent analysis to a detection StageRun, with a
  composite foreign key enforcing the same mission and a detection-only check.
  Retry allocates a new attempt and pins the original raster artifact.
- Creation, cancellation and retry no longer publish analysis Kafka commands.
  The existing scheduler, resource classes, tenant quotas, deadlines, shard
  execution/finalization and cleanup handle these runs.
- Analysis status is a transactionally updated projection of its StageRun.
  Monolithic progress uses completed tiles; sharded progress uses only durable
  receipts matching the exact plan checksum. Late attempts cannot overwrite
  a newer retry generation.
- The detector publishes a separate Manifest v3 workspace. Optional editable
  PostGIS features commit with the immutable artifact. Without indexing, reads
  use that final workspace; the old TileResultArtifact reader is now removed.
- Analysis runs are excluded from pipeline status projection, status-event
  application, dependency release and the pipeline detection layer. External
  artifact publication cannot bypass the analysis Job's atomic publisher.
- Mission locking uses PostgreSQL FOR NO KEY UPDATE to serialize changes
  without a foreign-key deadlock against concurrent artifact publication.
  A real concurrent publication/cancellation test covers this ordering.
- Frontend GeoJSON links use the owned analysis endpoint instead of raw CAS
  paths. Create/cancel and retry/download browser journeys were added.
- IA/processing Kafka entrypoints, workflow modules, Docker processing image,
  Helm Deployments and their exclusive tests are deleted. Reusable local
  detection exports moved to shared/detection_products.py.
- The supported deployment entrypoint requires an immutable Stage Job image
  tag. Compose is now compose.test.yaml, for test/control-plane dependencies
  only. All three compute Deployments are absent from Helm. The workspace PVC
  and work-drive validation now apply to Stage Jobs independently of the
  retired COLMAP Deployment. Model pinning moved to stageJobs.sam3.
- Fast v2, Normal v3, HQ v4, facade v3 and opacity SH remain supported.
  playcanvas-opacity.ts is still byte-for-byte unchanged from the base commit.

### Verification of the applied lot

- CPU: **1,598 passed, 1 skipped, 43 deselected**. The optional plyfile
  dependency is still absent; integration/GPU cases are excluded from this count.
- PostgreSQL/PostGIS: **6 passed**, including five analysis tests and the
  existing scheduler lock test. Coverage includes atomic geometry/artifact
  publication, rollback, cross-mission FK rejection, non-owner NOBYPASSRLS
  publication and tenant isolation, and concurrent publication/cancellation.
- Migration: full upgrade and empty-schema 0036→0037→0036→0037 round trip passed.
  Downgrade with analysis-linked Stage Jobs is explicitly refused; head stays
  0037.
- Frontend: **380 tests / 42 files**, ESLint, TypeScript and production build.
- Chrome Windows: **16 passed** (12 operator journeys and four GSTile cases).
  Rendered scene pixels are checked on NVIDIA Lovelace with
  isFallbackAdapter=false; no SwiftShader.
- Full make static and git diff --check passed. Helm lint and both protected
  overlay renders passed; retired-worker values and an invalid work drive are
  rejected. The preproduction Stage Job workspace PVC is rendered. The CI
  Compose combination validates with its http-e2e profile enabled.

The first new browser run had one ambiguous locator matching both the status
and phase text; the actual cancellation succeeded. The locator was narrowed
and the complete 16-case run passed. An initial manual Helm command supplied a
42-character test tag and was correctly rejected; renders with the exact
40-character repository SHA passed. A manual Compose validation without the
HTTP profile was rejected; the actual CI profile combination validates.

The existing local drone-ia:ci and drone-ia:sm89-e2e images failed a current-source
import smoke test because prometheus_client is absent. The current ia-extra
source and hash-locked requirements already include it. These images were not
rebuilt and are **not** qualified by this lot. Rebuild current API/control/IA
images and apply migration 0037 before the new campaign. No real detector
inference, deployed Kubernetes campaign or full scientific requalification is
claimed by the synthetic API/DB/browser fixtures.

Evidence under tmp/cleanup-20260828/:
analysis-cpu-final.log, analysis-static-final.log, analysis-postgis-final.log,
analysis-migration.log, analysis-downgrade-guard.log,
analysis-frontend-tests.log, analysis-frontend-lint.log, analysis-types.log,
analysis-frontend-build.log, analysis-browser-final.log,
analysis-helm-final.log and the two analysis-production.yaml and analysis-ovh-preprod.yaml renders.
Previous map and native evidence remains historical evidence for the earlier
lots; it is not counted as newly rerun here.

### Authorized COLMAP and global replay retirement

The user explicitly authorized the remaining COLMAP/bootstrap and global
resume removal on 28 August 2026. This follow-up is now applied:

- Removed the Kafka COLMAP entry point, worker, combined mission runner,
  fused publication/recovery modules and their exclusive tests.
- Kept the bounded reconstruction, training, filtering, rasterization and
  Gaussian-viewer executors. Their scientific modules and publication gates
  remain in the current path.
- Removed the global mission resume route, event builders, response state,
  frontend helper and Kafka fallbacks from mission/stage commands.
- Mission creation and stage retry now require Stage Jobs and fail with HTTP
  503 before opening a storage transaction when compute is disabled.
- Stage retries retain exact parent validation, idempotency, resource policy
  and immutable attempts. Cancelled/deleting missions now return HTTP 409
  instead of accepting a queued run that the scheduler would never execute.
  This also covers pending manual deletion. Cancellation is not cleared; a new
  mission is required in these cases.
- Removed the now-unreferenced worker inbox/messaging helpers, tile partition
  keys, worker lease settings and deferred-offset code used only by those
  leases. The transactional control-plane inbox/outbox, bounded delivery
  retries, dead-letter delivery checks and cancellation notifications remain.
- Removed the mission/orthomosaic/image-tile/tile-detection Kafka contracts.
  The generated schema now supports control, status and dead_letter only.
  Helm/Compose no longer provision the retired topics.
- Updated current runtime/deployment documentation. Historical migrations are
  not rewritten, and dated scientific evidence is not presented as a new
  qualification.

WorkerCancellationState and the PlayCanvas opacity-SH implementation were
compared directly with HEAD and remain byte-for-byte unchanged. Fast v2,
Normal v3, HQ v4 and facade v3 remain supported. The retention tile-journal
check and historical schema models are deliberately retained as previously
required; no retention guard was removed.

### Follow-up verification

- CPU: **1,558 passed, 1 skipped, 43 deselected**. The skip is the existing
  optional plyfile dependency; GPU and integration tests are excluded.
- Frontend: **380 tests / 42 files**, ESLint, TypeScript and production build.
- Chrome Windows: **16 passed** including four GSTile hardware-WebGPU cases.
  Rendered pixels are checked on NVIDIA Lovelace with
  isFallbackAdapter=false. No SwiftShader.
- Full make static and git diff --check pass.
- Helm lint and production/preproduction renders pass. Retired workers,
  topics and lease settings are absent; attempts to enable old workers or
  select an unknown work drive are rejected. The preproduction workspace PVC
  remains present. The HTTP-E2E Compose configuration validates.
- The targeted stage retry/retention suite passes **31 tests**, including
  five new refusal cases for cancelled/deleting missions. The current control
  transport suite passes **113 tests**.

During adaptation, tests caught an accidental replacement in the cancellation
outbox assertion, a timestamp comparison, a control fixture missing its command
and one remaining obsolete tile-identity test. These were corrected before
the complete final passing run. No failure of the current scientific path was
observed in these checks.

Evidence under tmp/cleanup-20260828/:
colmap-cpu-final.log, colmap-static-final.log, colmap-frontend-tests.log,
colmap-frontend-lint.log, colmap-types.log, colmap-frontend-build.log,
colmap-browser-final.log, colmap-transport-focused.log,
colmap-retry-guards.log, colmap-helm-final.log,
colmap-production.yaml and colmap-ovh-preprod.yaml.
before-colmap-retirement.patch captures the pre-follow-up working-tree diff.

The six PostGIS checks and migration round trip reported above belong to the
previous analysis lot; they were not rerun for this transport-only follow-up.
No current container image build, deployed Kubernetes run, real detector
inference or full scientific requalification is claimed. Rebuild the four
current application images and apply migration 0037 before the new campaign;
the previously tested local IA images are not qualified.

The authorized source retirement is complete. No live missions/data were
erased, no existing images/topics were deleted from a deployed environment,
and nothing was committed, pushed or deployed. Historical database structures
required by migrations and retention remain intentionally present.

## General review and release checks — 2026-08-28

The operator requested a general code, test, modularity and CI review, then a
pull request and merge only after the required checks pass. This section
supersedes the intermediate status statements above.

The review covered stage execution and generation fencing, analysis publication,
tenant/artifact boundaries, cancellation and deletion locks, schema constraints,
current native/viewer paths, packaging, deployment examples and CI selection.
The current separation between HTTP adapters, shared domain services and bounded
executors is retained. No additional framework or broad structural rewrite was
needed. Architecture, complexity, strict typing and zero-duplication checks remain
enabled.

### Findings fixed with regression tests

1. Global mission deletion could deadlock with artifact publication: a mission
   FOR UPDATE lock blocked the publisher's foreign-key lock while deletion waited
   for its stage lock. Both mission state acquisition and cancellation now use
   PostgreSQL FOR NO KEY UPDATE. A deterministic concurrent PostGIS test reproduced
   the deadlock before the fix and passes after it.
2. Standalone analysis admission checked an unused deletion-failure string.
   It now uses the shared manual-deletion state constants and rejects every
   cancelled/deleting state before creating an analysis.
3. The CI selector omitted split database model modules and migration verifier
   scripts from the real migration gate. A discovery-based regression covers
   all current database modules and migration verifiers.
4. COLMAP builds could consume a stale mutable base despite building a base
   tagged for the requested revision. The Dockerfile now requires an explicit
   COLMAP_BASE_IMAGE; deployment and publication scripts pass the same revision
   used by the application. Fake-Docker execution tests cover binding, missing-base
   refusal and CPU-only publication without performing a push.
5. Cartographic bounding boxes accepted NaN coordinates. All four coordinates
   must now be finite before bounds comparisons.
6. The non-owner RLS integration test assumed a database named droneai.
   It now quotes the actual connected database identifier and passes in an
   independently named disposable database.

### Verified locally

- Full Python static checks, strict type checking, shell/workflow validation,
  Markdown links and generated contracts pass.
- CPU coverage is **70%**, above the unchanged 60% gate.
- Zero-duplication check and strict Python vulnerability audit pass.
- Real PostgreSQL/PostGIS: identity and RLS migration verification; full
  base/head round trip; one-revision rolling data preservation; scheduler
  locking; six standalone-analysis publication, isolation and concurrency cases.
- Current native source: incremental rebuild and **8/8 CTest suites** on the
  NVIDIA GPU, including training and tile training.
- API/control and frontend images were rebuilt and pass non-root, read-only
  runtime import checks. IA and COLMAP image verification is recorded in the
  final delivery evidence once their builds finish.
- The unchanged frontend remains covered by the preceding **380 unit tests**
  and **16 Windows Chrome cases**, including four hardware WebGPU image checks
  on NVIDIA Lovelace with isFallbackAdapter=false. These browser results were
  obtained earlier in this cleanup, not rerun for the API/build-script fixes.

Required GitHub checks remain CI gate and CUDA validation gate with strict branch
protection. No protection, threshold or security check is bypassed. The optional
self-hosted GPU workflow is not a substitute for the explicit local GPU evidence.

### Deployment boundary

This is an intentionally breaking source cleanup. Rebuild the four application
images and the matching COLMAP base; apply migration 0037 before new analysis
Stage Jobs. The migration refuses downgrade once those analysis-bound stages
exist. Do not deploy the new API against revision 0036 or reuse old unversioned
mission artifacts. Existing mission data is not deleted by this change.

Fast v2, Normal v3, HQ v4, facade v3 and directional opacity SH are retained.
This review does not claim a new scientific profile qualification, real detector
inference or an end-to-end deployed Kubernetes campaign. These remain part of
the operator's planned new campaign. No production deployment is performed.

Evidence is retained under tmp/cleanup-20260828/review-*.log. Initial failing
regressions are preserved alongside passing runs.

### CI integration follow-up

The first PR integration run exposed committed fixture rows leaking into the
global outbox and retention tests. The standalone-analysis fixture now removes
only its own operational rows after each test, while retaining append-only audit
records.

That failure also revealed a real PostgreSQL defect: deleting a mission with
linked parent/child artifacts was rejected by the immediate parent RESTRICT
foreign key, even when both artifacts belonged to the mission being deleted.
Retention now removes only edges whose two endpoints belong to that mission,
inside the final deletion transaction. Foreign-key protection for dependencies
from another mission remains in place. Compute-quiescence, cancellation drain,
Kubernetes cleanup evidence, historical tile checks and retry guards are unchanged.

The existing real PostgreSQL retention test now includes a parent/child graph
and verifies the released byte total. It failed before the SQL correction and
passes afterward. The combined PostgreSQL, deletion and retention selection
passes **24 tests**. The corrected full CPU suite is rerun before merge.


The same concurrent publication test was extended to automatic retention draining
and reproduced a second deadlock. Candidate state transitions now also use
FOR NO KEY UPDATE; the final physical deletion retains its exclusive mission
lock. The new regression passes without weakening the quiescence conditions.


### Image security review

The second CI run was green, but inspection of its actual Trivy reports found
fixable HIGH findings inherited from pinned OS images: OpenSSL in API, IA and
frontend, plus the util-linux package family in the API. A green CRITICAL-only
gate was therefore insufficient to close this review.

Runtime Dockerfiles now explicitly refresh those packages. Image gates are
strengthened to reject fixable HIGH as well as CRITICAL findings, including
the CUDA workflow when selected; no ignore list or severity suppression is added.
The existing source-contract tests verify the stronger threshold. Final rebuilt
image scans and CI status are recorded in the PR delivery evidence.
