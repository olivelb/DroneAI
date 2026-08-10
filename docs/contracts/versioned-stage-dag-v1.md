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
GPU and minimum-VRAM requirements. Detection with SAM3 is promoted to the
high-memory GPU class, and a request may strengthen but never reduce its
stage's GPU envelope.

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
whose exact input stage is now available. The resulting Kafka command carries
the run UUID, attempt, selected single stage, exact upstream UUID map and stage
parameters.

## Ownership and compatibility

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

The fused COLMAP/DroneGS worker remains a compatibility path for existing and
deliberately local deployments. It treats an omitted `detection` phase as
terminal after raster publication, so no tiling or inference is launched
accidentally. New Kubernetes qualifications use the bounded executors below;
both paths call the same scientific stage boundaries and this migration did not
change CUDA, COLMAP or DroneGS versions.

The scheduler policy is deterministic and tenant-aware: oldest work is kept in
order within each owner, owners are served round-robin, and global, per-owner,
per-mission and per-resource-class limits all apply. Same-mission concurrency
is permitted only when neither DAG node is an ancestor of the other. Kubernetes
Job manifests are bounded (`activeDeadlineSeconds`, no retry, automatic TTL),
run as non-root with dropped capabilities and derive their requests/limits from
the persisted resource class. All five commands completed the representative
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
node selectors and (for GPU stages) the selected GPU architecture recorded in
provenance. Job pods receive only run/mission identity plus S3/Kafka settings
and Secret references for database/S3 credentials; they use bounded writable
`/tmp`, `/work` and `/cache` volumes over a read-only root filesystem. The
dashboard RBAC gains namespaced Job verbs only while this mode is enabled.

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
bytes, manifest bytes and elapsed time. The v1 transfer still republishes and
restores the complete workspace, so `reused_bytes` is intentionally zero; these
measurements are the baseline for the incremental/content-addressed manifest
migration rather than a claim that deduplication already exists.

The reader now normalizes deployed manifest v1 and strict
[`Artifact Manifest v2`](artifact-manifest-v2.md). The shared restore engine
resolves checksum-verified v2 parent overlays and supports bounded selection by
role and logical path. The writer remains v1 by default. A disabled Helm flag
can publish v2 CAS overlays with exact parents and stage-specific roles. A
second disabled flag lets only the detection adapter restore its exact declared
orthomosaic; it is rejected unless the v2 writer is active, and its output
continues to inherit every unmaterialized parent file. Other stage adapters
still request full restores. Blobs above 5 GiB use conditional multipart
completion and still require an endpoint qualification before activation. This
reader-first order preserves rollback compatibility during the canary.

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

The first fan-out foundation is implemented without changing that deployment
boundary. `shared.detection_sharding` partitions the complete row-major tile
sequence into compact, contiguous, bounded shards and assigns the plan a stable
SHA-256. It covers the 5,412-tile audit case with six shards at 1,024 tiles per
shard. `shared.detection_shard_results` rejects results for another plan,
out-of-range tile indices, missing or duplicate shards, model-provenance drift
and aggregate detection overflow before applying the existing global spatial
deduplication across shard boundaries. The current executor deliberately runs
one such plan shard and retains the 4,096-tile limit until indexed child Jobs,
their retry/cancellation lifecycle and the finalizer are durably orchestrated.
The Kubernetes manifest builder can now express an indexed Job with 2 to 256
completions, bounded parallelism and a shard index injected from the standard
`batch.kubernetes.io/job-completion-index` pod annotation. The ordinary Job
manifest remains unchanged. The orchestrator does not request indexed mode yet:
activation remains blocked on an idempotent durable shard receipt and explicit
finalizer lifecycle, rather than relying on successful pod exit alone.

Migration `0018` adds that durable receipt boundary. Each successful indexed
pod must record exactly one immutable result key, checksum and size for the
exact persisted plan and shard index. The stage-run row lock serializes
concurrent publications; an identical retry is idempotent, while a different
result for an existing index fails closed. A finalizer can obtain receipts only
when every index is present in order and every stored shard count, tile count,
key and digest still matches the durable plan. The orchestrator still does not
dispatch indexed detection until the S3 publication and finalizer executables
use this boundary end to end.

Shard-result JSON is serialized canonically and published through the same
conditional CAS writer as artifact blobs. Its receipt key must equal the
checksum-derived `blobs/sha256/...` key. Finalization downloads every ordered
receipt, verifies byte size and SHA-256 before parsing, rechecks the result
against the persisted plan, then passes only validated results to fan-in.

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
