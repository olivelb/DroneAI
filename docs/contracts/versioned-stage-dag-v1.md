# Versioned mission-stage DAG contract v1

## Scope

Migration `0014` adds an append-only execution graph below each owned mission.
The graph is versioned independently from the legacy service snapshot and uses
this ordered stage vocabulary:

1. `reconstruction`
2. `gaussian_training`
3. `gaussian_filtering`
4. `rasterization`
5. `detection`

`GET /mission/parameters` exposes the DAG version, stage identifiers and direct
dependencies. A new mission command includes the selected dependency-closed
phase set. The dashboard automatically selects required ancestors and removes
dependants when an ancestor is cleared.

## Durable records

`mission_stage_runs` stores one immutable attempt identity and its mutable
execution state. Its unique `(mission_id, stage, attempt)` tuple and SHA-256
idempotency key prevent duplicate retries. Parameters and exact upstream
artifact IDs are fixed when the attempt is queued; executor, provenance,
quality metrics, heartbeat and terminal error fields describe what actually
ran.

Migration `0016` adds the scheduling envelope: `resource_class`, bounded Job
identity, dispatch count/error and scheduling timestamp. The v1 catalogue
declares four portable classes (`cpu-standard`, `gpu-standard`,
`gpu-geometry`, `gpu-high-memory`) with explicit CPU, memory, ephemeral storage,
GPU and minimum-VRAM requirements. Detection with the pinned SAM3 revision,
1,008 px processor input and batch size one is promoted to the 12 GiB
`gpu-geometry` class. A request may strengthen but never reduce that envelope;
using the 24 GiB class does not implicitly increase the bounded batch size.

`mission_artifacts` assigns a UUID, kind, URI, SHA-256, optional size and
metadata to an output from exactly one stage run. An existing UUID can only be
published again when every immutable field and the complete parent set match.

`mission_artifact_parents` stores exact `derived_from` edges. Both ends must
belong to the same owner-scoped mission, and a self-edge is rejected. Products
and their public parent UUIDs are returned by `GET /missions/{vol_id}`.

## Retry and publication API

An operator requests a new attempt with:

```http
POST /missions/{vol_id}/stages/{stage}/runs
Idempotency-Key: a-client-generated-key-with-at-least-8-characters
Content-Type: application/json

{
  "parameters": {"confidence": 0.45},
  "upstream_artifact_ids": {
    "rasterization": "d671317d-9424-42ab-86c3-56adb0ea7685"
  }
}
```

The keys must exactly match the selected stage's direct dependencies. Each
artifact must exist in the same mission and must have been produced by the
named dependency stage. Reusing an idempotency key for the same request returns
the existing run; using it for another mission or stage returns `409`.

Bounded executors publish through the internal `shared.stage_execution`
transaction and do not call a public HTTP endpoint. The HTTP publication route
is an admin-only recovery/import operation. It accepts only the canonical
artifact kind for the run's stage, the exact stage-run workspace manifest URI,
the exact durable parent set, and a manifest whose S3 `sha256` metadata matches
the request. A new artifact is accepted only while the run is `running`:

```http
POST /missions/{vol_id}/stages/runs/{run_id}/artifacts
Content-Type: application/json

{
  "artifact_id": "c5b7c8fd-13c2-4df2-b3fd-b9fdcd69ab49",
  "kind": "reconstruction_workspace",
  "uri": "s3://drone-ai/missions/example/stage-runs/110d8de1-9b6f-4dd4-a7f1-480c0843ed74/reconstruction-workspace/manifest.json",
  "checksum_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "size_bytes": 123456,
  "metadata": {
    "manifest_key": "missions/example/stage-runs/110d8de1-9b6f-4dd4-a7f1-480c0843ed74/reconstruction-workspace/manifest.json"
  },
  "parent_artifact_ids": []
}
```

The route re-verifies S3 even for an idempotent replay; an unavailable,
unverified or differently addressed manifest is never trusted from request
metadata alone. Successful publication releases only blocked direct dependants
whose exact input stage is now available. The scheduler reads the queued row
and dispatches its exact run UUID, attempt, parent artifacts and parameters
to the bounded executor. No Kafka compute command is emitted.

## Ownership and execution

All reads and mutations reuse the mission ownership contract. Administrative
cross-owner access remains explicit and audited. Artifact IDs never weaken the
mission boundary.

Legacy worker status events are projected onto the latest matching stage run.
Entering a later stage closes non-terminal predecessors as succeeded, and a
generic worker `ERROR` or `CANCELLED` event is attached to the currently active
stage instead of resetting reconstruction. Existing missions without stage
runs keep their legacy detail projection. New executors include `stage_run_id`
in status events so a delayed event from an older attempt can never mutate the
newer attempt.

Bounded Stage Jobs are the only supported execution path. Mission creation and
stage retry return HTTP 503 when Stage Jobs are disabled. Cancelled/deleting missions reject new retries with HTTP 409 and require a
new mission; cancellation is never cleared by retry. Global mission replay
is removed; retries keep successful ancestors and use exact immutable inputs.
The COLMAP, CUDA and DroneGS scientific implementations are unchanged by this
transport retirement.

The scheduler policy is deterministic and tenant-aware: oldest work is kept in
order within each organization, organizations are served round-robin, and
global, per-organization, per-mission and per-resource-class limits all apply.
Organization and mission limits count logical stage runs. Global and resource
limits count physical resource units: an ordinary Job consumes one unit, while
an indexed detection Job consumes its effective pod parallelism. Same-mission
concurrency is permitted only when neither DAG node is an ancestor of the
other. Kubernetes
Job manifests are bounded (`activeDeadlineSeconds`, no retry, automatic TTL),
run as non-root with dropped capabilities and derive their requests/limits from
the persisted resource class. Rasterization uses `gpu-high-memory` whenever
the selected profile is `high-quality-*` or its configured Gaussian cap is
above the normal 3M envelope; this covers the large host-memory peak of final
GeoTIFF materialization independently of steady-state VRAM use. All five commands completed the representative
BIGZEN K3s/RTX 3090 Q3 chain. The generic chart default remains disabled only
to require an explicit immutable executor map for each environment; it is no
longer an adapter-availability limitation. `deploy.sh distributed` activates
the mode when `STAGE_JOBS_IMAGE_TAG` supplies the commit-derived image tag.

When explicitly enabled, the dashboard reserves queued rows with
`FOR UPDATE SKIP LOCKED`, commits their deterministic Job identity, then calls
the Kubernetes API. Before reading capacity or candidates, the transaction
must acquire the shared PostgreSQL advisory lock
`droneai-stage-scheduler-v1`; a replica that does not acquire it skips that
reservation tick. This preserves API-replica HA while serializing the global,
owner, mission and resource-class budgets. A crash between reservation and
creation is recovered by the next reconciliation tick; `409 AlreadyExists` is
success, and missing Jobs are recreated only up to the configured dispatch
bound. Active Jobs renew the stage heartbeat. Failed Jobs fail the run,
mission cancellation deletes the Job, and a Job that exits successfully
without first publishing its immutable artifact is treated as failed. Artifact
publication atomically marks the run succeeded before releasing dependants.

Activation requires a complete `stageJobs.executors` map for all five stages.
Every entry supplies an immutable image, a non-empty one-shot command, optional
additional node selectors, validated tolerations and (for GPU stages) the
selected GPU architecture recorded in provenance. The renderer derives GPU
presence and minimum-VRAM capability selectors from the run's resource class;
executor selectors may narrow but never contradict them. Job pods receive only
run/mission identity plus S3/Kafka settings and their stage-specific Secret
references for database/S3 credentials; they use bounded writable
`/tmp`, `/work` and `/cache` volumes over a read-only root filesystem. The
selected mission `work_drive` is persisted in every stage-run contract. The
control plane resolves that name only through the operator-owned Helm drive
catalog and mounts its declared `hostPath`, PVC or bounded `emptyDir` at
`/work`; a missing drive fails the run instead of silently consuming node-root
storage. The dashboard RBAC gains namespaced Job verbs only while this mode is
enabled.

## One-shot worker boundary

`shared.stage_execution` is the common boundary used by bounded executor
images. It claims only a run already reserved for `kubernetes-job`, verifies the
expected stage and exact immutable inputs, moves the run to `running`, and
maintains a background database heartbeat. Handlers receive typed mission/run
parameters and ordered artifact identities; long native subprocesses must call
the cooperative cancellation control at safe boundaries.

A successful handler returns one checksum-addressed result whose kind is fixed
by the stage contract. The boundary
creates its deterministic artifact UUID and exact parent edges, merges quality
metrics/provenance, marks the run succeeded, and releases direct dependants in
the same transaction. Exceptions and durable mission cancellation are terminal
and never release dependants. This lifecycle is shared so stage-specific
COLMAP, Gaussian, raster and detection adapters do not reimplement leases or
artifact consistency.

Stage-local disks are disposable. `shared.stage_workspace` transfers the
required workspace through S3 as a canonical manifest plus individually
verified files. Publication rejects symbolic links, records relative path,
size and SHA-256 for every file, and uses the manifest SHA-256 as the stage
artifact checksum. Restoration rejects absolute/traversal/duplicate paths and
removes any file whose downloaded size or digest differs. Both directions call
the cooperative cancellation hook between files. Every bounded adapter records
a versioned `workspace_transfer` provenance block for publication and, when
applicable, restoration: logical bytes, file count, transferred bytes, reused
bytes, manifest bytes and elapsed time.

The only supported workspace contract is
[Artifact Manifest v3](artifact-manifest-v3.md). All writers use tenant CAS
overlays with exact parents and stage-specific roles; all readers require
the durable mission organization and reject old formats. The detection-only
selective-restore flag remains disabled by default. When enabled, it restores
the declared orthomosaic while preserving every unmaterialized parent file in
the output overlay. Other stages request full restores. Conditional multipart
completion and provider qualification requirements are unchanged. Old runs
must be drained before deploying matching API/control and executor images.

The first bundled adapter now executes `reconstruction` in the COLMAP image:
it downloads the immutable dataset prefix, runs preparation, sparse mapping,
optional RTK refinement, undistortion/alignment, writes a versioned portable
COLMAP state file, and publishes the complete verified workspace. Absolute
local paths are rebased on restore, and paths outside the workspace are
rejected. Its Job always removes the local workspace in `finally`; no GPU or
CUDA implementation/version changes are part of this adapter.

The Gaussian workflow exposes `execute_gaussian_training_phase` as its first
explicit GPU boundary. It returns the prepared scene, merged unfiltered PLY
state, backend name and trainer binary SHA-256. The legacy fused call now uses
this same boundary, so subsequent Job extraction does not create a second
training implementation or alter established raster results.

`execute_gaussian_filtering_phase` is the next explicit boundary. It consumes
the training state, applies geo/facade alignment and the configured filter
chain exactly once, records Gaussian counts before/after, and returns the
filtered model with immutable render geometry. Keeping this state separate is
what prevents a later raster Job from applying Sim3/PCA transforms twice.

`execute_gaussian_rasterization_phase` consumes only that filtered render
state and produces raw RGB, height and extent buffers with explicit dimensions.
It cannot invoke training or filtering. The legacy workflow calls the same
function before its unchanged coverage gates and GeoTIFF writer.

The bounded detection adapter also records its deterministic tile-plan cost:
tile count, size, overlap, planned inference pixels and the ratio between those
pixels and the source raster. This makes overlap amplification and scenes above
the current single-Job envelope measurable before detection is split into
fan-out/fan-in shards.

`shared.detection_sharding` partitions the complete row-major tile
sequence into compact, contiguous, bounded shards and assigns the plan a stable
SHA-256. It covers the 5,412-tile audit case with six shards at 1,024 tiles per
shard. `shared.detection_shard_results` rejects results for another plan,
out-of-range tile indices, missing or duplicate shards, model-provenance drift
and aggregate detection overflow before applying the existing global spatial
deduplication across shard boundaries. The monolithic executor deliberately
retains its 4,096-tile safety limit; the separately gated fan-out path removes
that deployment ceiling by partitioning a larger validated plan.
The Kubernetes manifest builder can now express an indexed Job with 2 to 256
completions, bounded parallelism and a shard index injected from the standard
`batch.kubernetes.io/job-completion-index` pod annotation. The ordinary Job
manifest remains unchanged.

Migration `0018` adds that durable receipt boundary. Each successful indexed
pod must record exactly one immutable result key, checksum and size for the
exact persisted plan and shard index. The stage-run row lock serializes
concurrent publications; an identical retry is idempotent, while a different
result for an existing index fails closed. A finalizer can obtain receipts only
when every index is present in order and every stored shard count, tile count,
key and digest still matches the durable plan. A successful Indexed Job without
the complete exact receipt set fails closed before finalization.

Shard-result JSON is serialized canonically and published through the same
conditional CAS writer as artifact blobs. Its receipt key must equal the
checksum-derived `blobs/sha256/...` key. Finalization downloads every ordered
receipt, verifies byte size and SHA-256 before parsing, rechecks the result
against the persisted plan, then passes only validated results to fan-in.

The common execution boundary now separates stage authority from shard work.
`execute_stage_subtask` claims the same exact durable inputs and maintains the
same heartbeat/cancellation/failure semantics, but has no code path that can
create a `MissionArtifact` or release downstream stages. Durable plan
descriptors are reconstructed from their dimensions and rejected unless every
derived count, cost and SHA-256 matches exactly. The later finalizer alone will
retain `execute_one_shot_stage` publication authority.

The detection image now exposes three explicit execution modes through the
same immutable entry point: `monolithic` (the unchanged default), `shard` and
`finalizer`. A shard verifies the indexed Job count and its Downward-API index,
selectively restores the exact orthomosaic, executes only its planned windows
and publishes only a CAS receipt through `execute_stage_subtask`. The finalizer
requires all ordered receipts, verifies and aggregates them, selectively
restores the raster for georeferencing, then reuses the existing detection
workspace/GeoJSON publisher through `execute_one_shot_stage`.

The guarded scheduling phase is now implemented. At reservation time the
orchestrator resolves the exact raster artifact, reads its immutable width and
height metrics, builds and persists the complete plan, and keeps a one-shard
mission monolithic. A multi-shard plan creates an Indexed Job in `shard` mode.
Its requested parallelism is capped by the remaining global and GPU-class
units and by the shard count. Requested and effective parallelism plus the
physical unit reservation are persisted in provenance, so multiple tenants can
never create more simultaneous GPU pods than the configured envelope.
Only after Kubernetes completion and durable receipt validation does the same
stage run return to the scheduler for a separately named non-indexed Job in
`finalizer` mode. Finalization is aggregation, validation, geolocation and
publication rather than model inference, so it uses `cpu-standard` without GPU
selectors, runtime class or model credentials. Both the phase, inference
resource class and previous Job identity remain in provenance, and
deterministic names make recreation idempotent. Pre-accounting shard Jobs are
conservatively charged for every shard while active, and a missing legacy
finalizer is reconstructed on CPU. `stageJobs.detectionFanout.enabled` remains
`false` by default and the chart rejects it unless detection selective
restore is already enabled.

The corresponding handoffs use versioned JSON sidecars plus a PLY kept inside
the checksum-verified workspace. Each sidecar binds the model to the SHA-256 of
all deterministic product parameters while excluding Job-local paths and
callbacks. A retry with a changed resolution, profile, Gaussian cap, filtering
policy or qualification threshold is rejected instead of silently reusing an
incompatible model. The filtering sidecar records finite, shape-validated
alignment matrices, raster extent, camera coverage positions and reporting
summary; rasterization can therefore hydrate the filtered PLY without applying
alignment or filtering a second time. Artifact paths must exist below the
restored workspace and cannot escape it.

The COLMAP image now exposes bounded commands for `gaussian_training` and
`gaussian_filtering`. Training restores exactly one reconstruction workspace,
uses the shared recipe resolver and publishes the unfiltered PLY with backend,
trainer-binary and profile provenance. Filtering restores exactly one training
workspace, verifies that recipe before loading the PLY, reconstructs only the
lightweight scene metadata needed for alignment, and writes the filtered model
to a distinct path so its immutable parent is never overwritten. Both Jobs
reuse the common heartbeat/cancellation boundary and remove their disposable
workspace in `finally` on success or failure.

The `rasterization` command restores exactly one filtering workspace, hydrates
the already aligned/filtered model and calls the same raster boundary as the
legacy worker. A shared finalizer then applies the spatial-coverage gate,
vertical/geographic reference, RGB and height GeoTIFF writes, and facade report.
The resulting workspace publishes relative RGB, height and coverage paths plus
CRS/extent metadata; width, height, Gaussian count and the complete coverage
report are retained as quality metrics. This avoids a second raster
qualification implementation while keeping retries independent from training.

The `detection` command restores exactly one raster workspace and streams its
orthomosaic through bounded overlapping tiles without loading the complete
raster in memory. It uses the existing YOLO or SAM3 backend, checks cancellation
between tiles, caps both the tile plan and raw detection count, and requires one
validated model manifest to remain identical throughout the run. Pixel-space
results are projected back into the complete raster, deduplicated through the
shared overlap policy, then published as immutable JSON and WGS84 GeoJSON with
the exact model provenance. The disposable tile and restored workspaces are
removed on every stage exit. Mission `sam_prompt` and `tile_size` choices remain
part of the immutable stage parameters rather than being replaced by worker
defaults.

All five one-shot adapters are implemented and the complete artifact chain is
qualified by
[`../benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md`](../benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md).
Kubernetes dispatch still requires deliberate per-environment activation plus
a complete immutable executor-image map; the chart never infers that authority
from adapter availability alone.

## Invariants covered by tests

- dependency ordering, duplicate rejection and canonical idempotency;
- exact producer-stage validation for every upstream artifact;
- immutable artifact replay and changed-content rejection;
- admin-only recovery publication with canonical kind/URI/parents and remote
  S3 checksum verification;
- automatic release of the next ready stage;
- compatibility status projection and terminal-error attribution;
- owner-scoped API routes and versioned Kafka schema;
- PostgreSQL/PostGIS upgrade/downgrade round-trip for revision `0014`;
- resource-class selection and prevention of GPU-envelope downgrades;
- fair scheduling and every concurrency boundary;
- deterministic, hardened CPU/GPU Kubernetes Job rendering;
- transactional reservation, idempotent recreation, heartbeat, cancellation
  and artifact-publication reconciliation;
- one-shot claim, exact-input loading, cooperative cancellation, deterministic
  artifact publication and downstream release;
- safe, checksum-verified S3 workspace publication and restoration;
- portable COLMAP reconstruction state and bounded reconstruction adapter;
- checksum-bound Gaussian training/filtering handoffs, safe model paths and
  raster-state hydration without repeated transforms;
- bounded Gaussian training/filtering adapters, immutable parent PLY handling
  and cleanup on every exit path;
- shared raster qualification/finalization and a bounded rasterization adapter
  that cannot retrain or refilter its parent model;
- bounded raster streaming and YOLO/SAM3 detection, stable model provenance,
  overlap deduplication, GeoJSON publication and cleanup on stage completion;
- PostgreSQL/PostGIS migration round-trip through `0017`, including repair of
  pending legacy rasterization rows assigned to `cpu-standard`;
- two concurrent PostgreSQL scheduler transactions proving that only the lock
  owner can reserve capacity and that ownership transfers after commit.
