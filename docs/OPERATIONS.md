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

## Q3 acceptance record

Keep one dated Markdown report under `docs/benchmarks/` and record:

- Git commit and immutable image digests for every stage executor;
- Kubernetes version, node type, GPU model, VRAM, driver and CUDA runtime;
- dataset identity, input count/size and permission to retain the evidence;
- selected process, profile and effective overrides, including the exact
  Gaussian cap (Fast 1.5M, Normal 3M or High Quality 5M);
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

The one-shot executor implementation and complete artifact chain are qualified
on BIGZEN K3s/RTX 3090. Mission `chapelle-q3-five-jobs-20260809` exercised all
five Jobs, immutable S3 hand-offs, missing-Job reconciliation, a failed
rasterization followed by an exact-parent retry, automatic dependant release,
SAM3 CUDA inference and the multi-product operator view. The retained evidence
is the
[Chapelle Q3 addendum](benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md#q3-kubernetes-five-job-qualification-addendum).

`stageJobs.enabled=true` is therefore supported for controlled preproduction.
The generic chart default remains `false` so a deployment cannot acquire Job
RBAC or dispatch GPU work without an explicit immutable executor map. Each new
target environment must satisfy all of the following before activation:

1. All five executor entries use OCI digests or commit-derived immutable tags
   and the reviewed one-shot commands.
2. The Q3 record demonstrates the complete artifact chain on the target GPU.
3. Resource requests/limits fit the live quota and one active Job cannot force
   an unreviewed second GPU node.
4. Database backup and isolated restore have succeeded after the latest schema
   migration.
5. Job cancellation, deadline expiry, missing-Job reconciliation and a retry
   with a new attempt identity have been observed.
6. The operator can find stage status, heartbeat age, artifact checksum,
   quality metrics and exact failure reason from the mission detail view.
7. A rollback revision and deep-sleep procedure have been reviewed.

Enabling the flag is a reviewed deployment change, not a code-side default. On
the single-node distributed installer, set `STAGE_JOBS_IMAGE_TAG` to the exact
Git SHA; leaving it unset deliberately selects compatibility workers.
Production promotion remains blocked until its own cancellation/deadline,
backup/restore, rollback and interruption drills are recorded, even though the
preproduction execution path is qualified.

## Backup and retention

The durable recovery set is:

- PostgreSQL/PostGIS backup containing mission, stage, artifact and audit rows;
- versioned S3 mission objects and checksum manifests;
- remote versioned Terraform state plus its lock configuration;
- Git commit, image digests and the applicable Q3 report.

Kafka messages, Kubernetes Jobs/pods, local workspaces, tile JPEGs and restored
stage directories are disposable. They are not backups. A database dump is
accepted only after an isolated restore test; downloading an object is not a
restore rehearsal.

Current safe retention policy:

- keep seven rotating daily PostgreSQL slots in preproduction;
- keep published mission products and their immutable parents until an
  owner-authorized retention operation exists and has exported the useful
  products first;
- keep audited feature tombstones and edit events with their mission;
- never lifecycle-delete Terraform state or its most recent known-good version;
- delete temporary uploads, failed multipart parts and disposable Job prefixes
  only through a bounded, dry-run-capable cleanup operation.

An expired mission must be removed as one reviewed retention transaction:
database rows, derived objects and previews are inventoried by owner and mission
before deletion. S3 age alone is not sufficient because immutable parent edges
can still reference an older product.

## Failure recovery

| Observation | Safe response |
|---|---|
| Queued run has no Job | Let the bounded reconciler recreate it within its dispatch limit; do not create an untracked pod. |
| Heartbeat is stale | Treat it as delayed observability, inspect the Job and node, and do not infer failure solely from silence. |
| Job failed or exceeded its deadline | Preserve logs and durable error, then create a new stage attempt against the same exact parents. |
| Artifact checksum, canonical manifest URI or parent replay conflicts | Stop publication and investigate; never overwrite or relabel the existing artifact. The admin recovery route must successfully reverify the S3 manifest metadata. |
| Mission is cancelled | Keep it terminal, remove active Jobs and do not release dependants. |
| Database is unavailable/corrupt | Stop writers, restore into an isolated instance, validate migrations/counts, then perform a reviewed cutover. |
| S3 object is missing | Stop downstream retries and recover the exact version/checksum; do not substitute a similarly named object. |
| Deployment regression | Roll back to the recorded Helm revision and immutable image digests; schema rollback requires its own tested procedure. |

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
