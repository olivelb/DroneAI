# Qualification and operations contract

This document defines the evidence required to activate and operate bounded
stage Jobs safely. The detailed OVHcloud commands remain in the
[preproduction runbook](OVHCLOUD_PREPROD.md); this contract applies to every
Kubernetes environment.

## Qualification levels

| Level | Trigger | Required evidence | Automatic in pull requests |
|---|---|---|---|
| Q0 — contracts | Every code change | Static analysis, CPU tests, contract and documentation checks | Yes |
| Q1 — images | A service image or import boundary changes | Affected image build/import smoke test and immutable image digest | Only for affected services |
| Q2 — GPU runtime | CUDA/COLMAP version, GPU architecture, CTest or native GPU behavior changes | `nvidia-smi`, affected CTests and a small representative GPU mission | No; explicit dispatch |
| Q3 — platform E2E | Before first activation on a GPU target or promotion of that environment | Complete five-stage mission, retry, cancellation and recovery evidence | No; explicit dispatch |

Q2 and Q3 are intentionally manual, scoped qualifications. A pull request or
merge with no relevant CUDA, COLMAP, GPU-architecture or CTest change must not
repeat those long tests. The CI selector and its skipped result are part of the
evidence.

## Platform support and tenant access recovery

Provision a platform support identity only from an audited operator database
session. First run the preview, then repeat with `--apply` after review:

```bash
python3 -m tools.manage_platform_support \
  --action provision \
  --subject support@example.com \
  --credential-name primary-workstation \
  --actor-subject platform-operator
```

Store the one-time `dps` token immediately in the approved secret manager.
Support can inspect organization metadata and suspend/reactivate a customer,
but cannot see missions, datasets, tenant identities, policies or usage. It
rotates or revokes only its own credentials under `/platform/credentials`.
Review every mutation through `/platform/audit-events`.

If a support identity is suspected compromised, run the same command first
with `--action suspend` and no credential name, inspect the revocation count,
then repeat with `--apply`. This invalidates all raw tokens and signed sessions.
After incident review, `--action reactivate --credential-name <new-name>`
creates a new token; it never revives a previously revoked credential.

Tenant admins distribute one-time invitations from `/auth/invitations`.
Durable members should create a personal recovery token under
`/auth/recovery-tokens`, store it offline and revoke it after replacement.
Both use the public `/auth/capabilities/redeem` endpoint and are single-use.
Platform privilege alone cannot mint a tenant capability or impersonate its
holder; redemption still requires possession of the one-time bearer secret.
The full invariants and emergency limits are in
[`contracts/platform-identity-boundary-v1.md`](contracts/platform-identity-boundary-v1.md).

## Conditional multipart storage gate

Before enabling `stageJobs.artifactManifestV2WriteEnabled` on an S3-compatible
provider, qualify its real endpoint:

```bash
scripts/deploy/qualify-ovh-s3-multipart.sh
```

The command obtains the existing scoped OVH Object Storage credentials from
the Terraform outputs without printing them. It uploads one random 6 MiB CAS
object through multipart completion, confirms that `If-None-Match: *` rejects
an overwrite, verifies size/checksum and reuse, then deletes the probe and
verifies cleanup. A failure blocks the v2 writer rollout; never replace the
conditional completion with an unconditional fallback.

Roll out incremental materialization and fan-out in three independently
reversible steps:

1. set `stageJobs.artifactManifestV2WriteEnabled=true` while keeping
   `stageJobs.artifactSelectiveRestoreEnabled=false`, then qualify a complete
   five-Job product and its v1-reader rollback;
2. set `stageJobs.artifactSelectiveRestoreEnabled=true` for the detection
   canary, verify that restore transfer bytes contain only the declared
   orthomosaic and that the resulting artifact still resolves every parent
   file plus the detection products;
3. set `stageJobs.detectionFanout.enabled=true` only for a large-raster canary,
   verify the persisted plan, every indexed shard receipt, the distinct
   finalizer Job and the final immutable detection artifact. Also run a small
   raster and confirm that it remains monolithic.

The chart rejects step 2 without step 1 and step 3 without step 2. To roll back,
disable detection fan-out first, then disable selective restore after verifying
one full detection restore, and finally disable the v2 writer.
Do not enable selective restore for reconstruction, Gaussian training,
filtering or rasterization without a separate contract and qualification.

## Scoped credential gate for stage Jobs

Every one-shot executor in `staging` or `production` must use its own existing
Kubernetes Secret. Configure all five names under
`stageJobs.credentialSecrets`; Helm and the API both fail closed if an entry is
missing or if two stages share a Secret. Each Secret exposes
`stage-database-url`, `s3-access-key` and `s3-secret-key`. The database URL must
authenticate as a non-owner `NOBYPASSRLS` stage role; protected executors fail
before claiming work when PostgreSQL reports that RLS is inactive. Detection receives its
separate read-only Hugging Face token in addition to its stage credentials.

The distinct Secret names are only the enforceable deployment boundary. Before
activation, verify that their values identify distinct database and object
storage principals: database roles must have no schema, role or database
administration privilege, and S3 policies must be limited to the DroneAI bucket
and the exact read/write/delete operations needed by the executor. Account for
immutable upstream manifests and organization-scoped
`organizations/{organization_id}/blobs/sha256/` CAS objects when writing those
policies. Historical global CAS reads are migration compatibility only. Do not
claim credential isolation merely by copying the same principal into five
differently named Secrets.

Development deployments may omit the map and retain the shared
`storage.existingSecret` fallback so the single-node BIGZEN workflow remains
reproducible. Staging and production require bounded stage Jobs and reject this
fallback as well as all fused compute workers.

## GPU capability scheduling gate

Bounded Jobs derive node selectors from their resource class. GPU classes
require `nvidia.com/gpu.present=true` plus exactly one cumulative capability
label: `droneai.io/gpu-vram-at-least-8gb`, `-12gb` or `-24gb`. A 24 GB node
must carry all three labels so it remains eligible for smaller classes. These
are operator assertions about the physical node SKU, not arbitrary mission
parameters; verify them against the provider inventory and `nvidia-smi` before
labelling a node.

The BIGZEN distributed installer labels all applicable thresholds from the
smallest physical GPU it observes and logs the resulting advertised GB class.
`DRONEAI_GPU_VRAM_CLASS_GB` is an explicit operator override for hardware whose
reported usable MiB does not map cleanly to its reviewed SKU; never use it to
overstate capacity.

Stage Jobs resolve the mission `work_drive` from
`colmapWorker.workVolume.drives`. Verify the rendered Job's `work` volume before
a large run: a configured `hostPath` must be the real mounted disk path and a
cloud workspace must reference the expected bound PVC. The scheduler fails a
run whose selected drive disappeared. Do not replace that failure with a root
`emptyDir`: restoring and rasterizing a large CAS workspace can cross kubelet's
ephemeral-storage eviction threshold. The HQ raster class is
`gpu-high-memory` (24 GiB request, 64 GiB limit); normal rasterization remains
`gpu-standard` while its Gaussian cap is at most 3M.

Executor-specific `node_selector` entries may further restrict a pool or GPU
architecture, but cannot contradict the resource-class selectors. Executor
`tolerations` accept only explicit non-empty taint keys and validated Kubernetes
operators/effects. Match the real GPU-pool taint; do not use a broad empty-key
`Exists` toleration. During Q3, retain the rendered Job selector/tolerations and
the selected node labels as scheduling evidence.

## Q3 acceptance record

Keep one dated Markdown report under `docs/benchmarks/` and record:

- Git commit and immutable image digests for every stage executor;
- Kubernetes version, node type, GPU model, VRAM, driver and CUDA runtime;
- rendered resource-class node selectors, executor tolerations and selected
  node capability labels;
- dataset identity, input count/size and permission to retain the evidence;
- selected process, profile and effective overrides, including the requested,
  surface-derived, VRAM-derived and final Gaussian caps from stage provenance;
- one run ID and artifact ID/checksum for every selected stage;
- exact parent-artifact edges, model manifest and AI prompt/classes;
- duration, peak CPU/RAM/VRAM and temporary/persistent storage use per stage;
- workspace logical/file/manifest bytes, transferred/reused bytes and transfer
  duration from the versioned `workspace_transfer` provenance block;
- detection tile count, planned inference pixels and overlap amplification;
- for a fan-out canary, plan checksum, shard count/parallelism, durable receipt
  count, shard and finalizer Job identities, and finalizer retry evidence;
- quality-gate reports, final RGB/height GeoTIFF checks and detection counts;
- one new detection attempt against an existing raster and one cancelled or
  failed stage followed by a new immutable attempt;
- confirmation that the disposable Job workspaces and pods were removed.

Never store credentials, signed URLs, bearer tokens, private datasets or raw
Terraform state in the report.

## Production drill evidence

The production cancellation, deadline, interruption, restore and rollback
drills use the machine-readable
[`production-qualification-evidence-v1`](contracts/production-qualification-evidence-v1.schema.json)
contract. The contract is stricter than a narrative report: it requires the
exact release and target, all five executor image digests, RTO/RPO objectives,
the nine mandatory drills and an operator attestation. It also rejects fields
or text that look like credentials.

Create one deliberately non-passing draft before the exercise:

```bash
python3 tools/production_qualification.py init \
  --qualification-id ovh-preprod-2026-08-10 \
  --environment ovh-preprod \
  --cluster droneai-gra11 \
  --namespace drone-ai-preprod \
  --operator admin@olembo.fr \
  --output docs/benchmarks/ovh-preprod-2026-08-10.qualification.json
```

Fill the target metadata, immutable digests, timestamps, observed RTO/RPO and
bounded evidence references after each operator-run drill. Evidence references
may identify retained Kubernetes captures or run/artifact records, but must not
contain logs, URLs or secret values themselves. Then validate, render and apply
the blocking gate:

```bash
python3 tools/production_qualification.py validate \
  docs/benchmarks/ovh-preprod-2026-08-10.qualification.json
python3 tools/production_qualification.py render \
  docs/benchmarks/ovh-preprod-2026-08-10.qualification.json \
  --output docs/benchmarks/ovh-preprod-2026-08-10-qualification.md
python3 tools/production_qualification.py gate \
  docs/benchmarks/ovh-preprod-2026-08-10.qualification.json
```

`validate` checks format and secret safety but permits an unfinished record.
Only `gate` requires every drill to pass within the recorded objectives. CI
validates every tracked `*.qualification.json`; it never executes the
destructive drills, reserves a GPU or rebuilds CUDA/COLMAP.

The operator performs drills from a dedicated test mission and records the
following safe outcomes:

| Drill | Required observation |
|---|---|
| Five-stage chain | Exact immutable parent chain and final products are present. |
| Stage cancellation | Active Job disappears, mission remains terminal and no dependant is released. |
| Stage deadline | Durable timeout reason is visible and a new immutable attempt can be created. |
| Missing Job reconciliation | Tracked Job/pod removal is detected and recovered without an untracked pod. |
| API restart after reservation | At most one Job owns the reserved attempt after restart. |
| Database interruption | Writers stop safely and resume without duplicate completion. |
| Object-storage interruption | Publication fails closed; no partial artifact is marked successful. |
| Backup/restore | Isolated restore passes migrations, row/edge checks and artifact checksum reads. |
| Helm rollback | Recorded revision and immutable images restore service without corrupting newer rows. |

Do not reuse target-specific results: BIGZEN evidence qualifies BIGZEN, while
OVHcloud production needs its own evidence against the exact promoted release.

## Activation status and gate for bounded Jobs

The one-shot executor implementation, complete artifact chain and nine
production drills are qualified on BIGZEN K3s/RTX 3090. The original mission
`chapelle-q3-five-jobs-20260809` exercised retries, immutable hand-offs and the
operator view. The current follow-up observed all three derived VRAM selectors
across a successful five-Job chain, parsed a strict Manifest v2, then processed
4,160 SAM3 tiles through five Indexed shards, five durable receipts and a
separate finalizer. The retained evidence is the
[current scheduling/fan-out benchmark](benchmarks/bigzen-stage-jobs-fanout-2026-08-10.md),
the validated
[BIGZEN production qualification](benchmarks/bigzen-preprod-2026-08-10-qualification.md)
and the earlier
[Chapelle Q3 addendum](benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md#q3-kubernetes-five-job-qualification-addendum).

The same benchmark records the BIGZEN operator multi-product gate. The mission
catalogue and detail screens exposed scoped progress, logs and immutable
product checksums; the map resolved Manifest v2/CAS rasters and detections;
mission changes produced `1 → 10 → 0 → 1` scoped object counts; and a queued
SAM3 analysis remained cancelled after reload without dispatching a GPU Job.
The subsequent production exercise passed cancellation, deadline, missing-Job
reconciliation, API restart, PostgreSQL and MinIO interruptions, isolated
backup/restore and Helm rollback within the declared objectives. Its automated
gate remains blocked only until the named operator supplies the review
timestamp. None of these target-specific results qualifies OVHcloud.

The focused requalification reused the existing CUDA/COLMAP base because its
versions and GPU architecture were unchanged. It also found that the pinned
SAM3 processor resizes inputs to 1,008 px while the sampled batch-one footprint
was about 6.3 GiB. Commit `745e681...` then encoded the immutable revision,
effective resolution and batch-one policy and passed a focused 81-tile GPU
rerun using the 12 GiB `gpu-geometry` class. SAM3 source tiles are capped at
1,024 px, and the runtime manifest records the 1,008 px processor target and
batch size. Keep 8 GiB ineligible: the 12 GiB class provides headroom above the
sampled 6,334 MiB total footprint. A 24 GiB override remains valid but does not
increase batch size in this policy. Requalify any new revision, dtype, batch
size or GPU architecture before changing that envelope.

The default SAM3 confidence is 0.75 for new requests that omit a threshold.
It is a conservative candidate filter, not an acceptance gate. Production AI
qualification requires labelled truth and a threshold sweep reporting
precision, recall and false positives; the exploratory 0.20 and 0.50
Villesèque campaigns are not qualifying evidence. Do not claim vegetation
classification from SAM3 until a dedicated semantic model benchmark has been
accepted.

`stageJobs.enabled=true` remains supported for controlled preproduction only
after the target satisfies the current qualification gates. The generic chart
default remains `false` so a deployment cannot acquire Job
RBAC or dispatch GPU work without an explicit immutable executor map. Each new
target environment must satisfy all of the following before activation:

1. All five executor entries use OCI digests or commit-derived immutable tags
   and the reviewed one-shot commands.
2. All five credential Secrets and their distinct least-privilege principals
   have been provisioned and reviewed.
3. The Q3 record demonstrates the complete artifact chain on the target GPU.
4. Resource-class capability labels and executor tolerations match the reviewed
   GPU node pool and have been observed on the scheduled Jobs.
5. Resource requests/limits fit the live quota and one active Job cannot force
   an unreviewed second GPU node.
6. Database backup and isolated restore have succeeded after the latest schema
   migration.
7. Job cancellation, deadline expiry, missing-Job reconciliation and a retry
   with a new attempt identity have been observed.
8. The operator can find stage status, heartbeat age, artifact checksum,
   quality metrics and exact failure reason from the mission detail view.
9. A rollback revision and deep-sleep procedure have been reviewed.

Enabling the flag is a reviewed deployment change, not a code-side default. On
the single-node distributed installer, set `STAGE_JOBS_IMAGE_TAG` to the exact
Git SHA; leaving it unset deliberately selects compatibility workers.
Production promotion remains blocked until the target environment has its own
cancellation/deadline, backup/restore, rollback and interruption record, plus a
named operator attestation, even though the BIGZEN preproduction execution path
is qualified. Never roll back BIGZEN below Helm revision 24: revision 21
predates idempotent cancellation cleanup and was retired during the drill.

## Backup and retention

The durable recovery set is:

- PostgreSQL/PostGIS backup containing mission, stage, artifact and audit rows;
- versioned S3 mission objects, GCP source/marking rows, content-addressed GCP
  bundles and checksum manifests;
- remote versioned Terraform state plus its lock configuration;
- Git commit, image digests and the applicable Q3 report.

Kafka messages, Kubernetes Jobs/pods, local workspaces, tile JPEGs and restored
stage directories are disposable. They are not backups. A database dump is
accepted only after an isolated restore test; downloading an object is not a
restore rehearsal.

Current safe retention policy:

- keep seven rotating daily PostgreSQL slots in preproduction;
- keep published mission products indefinitely while the organization's
  `retention_days` is null;
- configure `retention_days` only after the customer export/legal-hold policy is
  reviewed through the operator-only policy command;
- keep audited feature tombstones and edit events with their mission;
- never lifecycle-delete Terraform state or its most recent known-good version;
- let the upload reconciler delete temporary/failed multipart state and record
  the corresponding organization storage release.

An expired terminal mission is claimed by the elected control worker, which
marks it `deleting`, removes its exact durable S3 prefix, deletes the database
graph and appends an immutable usage event. Failure becomes
`deletion_failed` and is retried only after the configured backoff. S3 age alone
is not sufficient because immutable parent edges can still reference an older
product. Dataset inputs are retained separately because several missions may
reference the same catalogue row. The full v1 boundary and provisioning command
are in
[`contracts/organization-saas-policy-v1.md`](contracts/organization-saas-policy-v1.md).

### Legacy resource adoption

Historical `legacy-unassigned` datasets and terminal missions are adopted only
with the operator command documented in
[`contracts/legacy-adoption-v1.md`](contracts/legacy-adoption-v1.md). Run the
default read-only plan first, archive its JSON, review every source/target
prefix, the physical `target_write_bytes` and logical quota impact, then pass
the freshly recomputed checksum to `--apply`. Stop writers for the selected
database graphs and S3 prefixes throughout this maintenance sequence.

Never delete the legacy source after the command reports success. First verify
the tenant catalogue/API, restore at least one adopted artifact from its v3
manifest, confirm the append-only `legacy_adoption_completed` event, and apply
the ordinary backup/legal-hold policy. A failed run may leave safe verified
copies in the target prefix. After fixing the cause, reuse the selection and
`run_id` only if the freshly regenerated plan has the same checksum; otherwise
review a new plan and use its new `run_id`. Never manually relabel rows or
overwrite objects.

## Failure recovery

| Observation | Safe response |
|---|---|
| Dataset upload remains `initializing` | Inspect `last_error` and S3 create/list/abort permissions. Retry the exact create request or wait for the reconciler; it aborts any unrecorded exact-key multipart handle before creating a new one. |
| Dataset file remains `completing` | Do not abort or delete the key. Retry the same completion request or wait for reconciliation; a matching object is adopted from `HEAD` without replaying S3 completion. |
| Dataset upload remains `finalizing` | Do not delete the manifest or file objects. Retry the session completion or wait for reconciliation; the stable timestamp makes manifest publication idempotent before catalogue commit. |
| Queued run has no Job | Let the bounded reconciler recreate it within its dispatch limit; do not create an untracked pod. |
| Heartbeat is stale | Treat it as delayed observability, inspect the Job and node, and do not infer failure solely from silence. |
| Job failed or exceeded its deadline | Preserve logs and durable error, then create a new stage attempt against the same exact parents. |
| Artifact checksum, canonical manifest URI or parent replay conflicts | Stop publication and investigate; never overwrite or relabel the existing artifact. The admin recovery route must successfully reverify the S3 manifest metadata. |
| Mission is cancelled | Keep it terminal, remove active Jobs and do not release dependants. |
| Database is unavailable/corrupt | Stop writers, restore into an isolated instance, validate migrations/counts, then perform a reviewed cutover. |
| S3 object is missing | Stop downstream retries and recover the exact version/checksum; do not substitute a similarly named object. |
| Deployment regression | Roll back to the recorded Helm revision and immutable image digests; schema rollback requires its own tested procedure. |
| Legacy adoption failed | Keep source objects untouched. Inspect the durable `legacy_adoption_failed` event and fix the reported object/DB conflict. Resume with the recorded `run_id` only when the regenerated plan checksum is unchanged; otherwise review and start a new run. |

The upload reconciler runs before expired-upload cleanup on every
`DRONEAI_UPLOAD_CLEANUP_SECONDS` interval. Inspect pending states without
modifying them manually:

```sql
SELECT session_id, dataset_name, status, updated_at, last_error
FROM dataset_upload_sessions
WHERE status IN ('initializing', 'finalizing')
   OR EXISTS (
       SELECT 1
       FROM dataset_upload_files
       WHERE upload_session_id = dataset_upload_sessions.id
         AND status = 'completing'
   )
ORDER BY updated_at;
```

Repeated `last_error` values indicate a storage permission/provider problem or
a database outage, not a scientific-data failure. Keep those incidents in the
SaaS operational queue. Migration `0023` is reversible for deployment rollback,
but its downgrade deliberately removes sessions that never obtained an S3
handle and converts uncertain completions to `failed`; run it only after the
new API writers are stopped and multipart state has been inventoried.

For compatibility Kafka workers, an unassigned consumer is not necessarily
lost: replicas may legitimately outnumber topic partitions. The assignment
watchdog therefore recycles only a consumer that was previously assigned, lost
all assignments, and did not rejoin within
`KAFKA_CONSUMER_UNASSIGNED_TIMEOUT_SECONDS` (60 seconds by default). A consumer
that has never received a partition remains idle without causing group
rebalances. Investigate repeated assignment-loss warnings and consumer lag
before scaling or restarting workers manually.

## Cost and capacity controls

Before a Q2/Q3 run, record the current quotas and price-bearing resources. Keep
GPU pools at zero nodes while idle, cap concurrent GPU resource classes, use
`activeDeadlineSeconds`, zero Job retries and automatic Job TTL, and verify that
autoscaling cannot wake an environment placed in deep sleep. Persistent volumes,
load balancers, gateways, registries and retained S3 bytes can remain billable
when application replicas are zero.

After a qualification run, verify zero unexpected Jobs/pods/nodes and compare
the actual durations/storage footprint with the report. The OVHcloud-specific
inventory, backup, deep-sleep and wake-up commands are maintained in
[OVHCLOUD_PREPROD.md](OVHCLOUD_PREPROD.md#10-deep-sleep-and-recovery).

## Operational views

The minimum operator view is the mission detail screen plus Kubernetes events
and logs. Until a dedicated Prometheus/Grafana stack is deployed, capture these
signals during every Q3 run:

```bash
kubectl -n drone-ai-preprod get jobs,pods -o wide
kubectl -n drone-ai-preprod get events --sort-by=.lastTimestamp
kubectl -n drone-ai-preprod logs job/<stage-job> --all-containers=true
kubectl -n drone-ai-preprod top pods
kubectl -n drone-ai-preprod get pvc
helm history drone-ai -n drone-ai-preprod
```

The future alerting baseline is: stage queue age, heartbeat age, failed/expired
Jobs, dispatch exhaustion, Kafka consumer lag, PostgreSQL/PVC capacity, S3
publication failures, GPU allocation and node autoscaling. Alerts must link to
mission and run identities without exposing owner data or credentials.
