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

A trusted operator or executor publishes an output with:

```http
POST /missions/{vol_id}/stages/runs/{run_id}/artifacts
Content-Type: application/json

{
  "artifact_id": "c5b7c8fd-13c2-4df2-b3fd-b9fdcd69ab49",
  "kind": "orthomosaic",
  "uri": "s3://droneai/missions/example/orthomosaic.tif",
  "checksum_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "size_bytes": 123456,
  "metadata": {"crs": "EPSG:2154"},
  "parent_artifact_ids": [
    "d671317d-9424-42ab-86c3-56adb0ea7685"
  ]
}
```

Publishing an artifact releases only blocked direct dependants whose exact
input stage is now available. The resulting Kafka command carries the run UUID,
attempt, selected single stage, exact upstream UUID map and stage parameters.

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

The current fused COLMAP/DroneGS worker remains supported during the executor
migration. It now treats an omitted `detection` phase as terminal after raster
publication, so no tiling or inference is launched accidentally. Earlier stop
points and restarts still use the durable command boundary introduced here and
will move to bounded per-stage jobs in the resource-aware orchestration phase;
this phase deliberately does not change CUDA, COLMAP or DroneGS versions.

The scheduler policy is deterministic and tenant-aware: oldest work is kept in
order within each owner, owners are served round-robin, and global, per-owner,
per-mission and per-resource-class limits all apply. Same-mission concurrency
is permitted only when neither DAG node is an ancestor of the other. Kubernetes
Job manifests are bounded (`activeDeadlineSeconds`, no retry, automatic TTL),
run as non-root with dropped capabilities and derive their requests/limits from
the persisted resource class. Job mode is disabled by default until the fused
workers expose and qualify one-shot per-stage commands; the existing Kafka
workers therefore remain the safe runtime default during this migration.

When explicitly enabled, the dashboard reserves queued rows with
`FOR UPDATE SKIP LOCKED`, commits their deterministic Job identity, then calls
the Kubernetes API. A crash between reservation and creation is recovered by
the next reconciliation tick; `409 AlreadyExists` is success, and missing Jobs
are recreated only up to the configured dispatch bound. Active Jobs renew the
stage heartbeat. Failed Jobs fail the run, mission cancellation deletes the
Job, and a Job that exits successfully without first publishing its immutable
artifact is treated as failed. Artifact publication atomically marks the run
succeeded before releasing dependants.

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

A successful handler returns one checksum-addressed result. The boundary
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
the cooperative cancellation hook between files.

## Invariants covered by tests

- dependency ordering, duplicate rejection and canonical idempotency;
- exact producer-stage validation for every upstream artifact;
- immutable artifact replay and changed-content rejection;
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
- PostgreSQL/PostGIS `0015 -> 0016 -> 0015 -> 0016` migration round-trip.
