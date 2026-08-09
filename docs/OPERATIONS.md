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
- quality-gate reports, final RGB/height GeoTIFF checks and detection counts;
- one new detection attempt against an existing raster and one cancelled or
  failed stage followed by a new immutable attempt;
- confirmation that the disposable Job workspaces and pods were removed.

Never store credentials, signed URLs, bearer tokens, private datasets or raw
Terraform state in the report.

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
| Artifact checksum or parent replay conflicts | Stop publication and investigate; never overwrite or relabel the existing artifact. |
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
