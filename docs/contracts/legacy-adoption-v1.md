# Legacy resource adoption contract v1

## Purpose and scope

This workflow moves the ownership boundary of explicitly selected historical
datasets and missions from `legacy-unassigned` to one existing organization.
It is an operational SaaS migration. It does not alter reconstruction,
Gaussian, raster, GCP or detector parameters and does not qualify scientific
results.

The workflow is deliberately copy-and-switch:

1. inventory PostgreSQL rows and every source object;
2. produce a read-only plan and checksum;
3. copy objects into the organization namespace without deleting sources;
4. convert global Artifact Manifest v2 and CAS references to tenant v3;
5. revalidate every source identity;
6. transactionally rebind PostgreSQL and append durable audit events.

There is no implicit discovery from unmanaged S3 roots and no destructive
move. Legacy source objects remain available for reviewed cleanup after backup
and retention decisions.

## Eligibility and fail-closed rules

The target organization must exist and be active. The requested owner must be
an active member of that organization. A resource is eligible only when:

- its durable organization is exactly `legacy-unassigned`;
- its dataset or mission prefix is the canonical legacy prefix;
- a dataset is `ready` and has a valid v1 manifest;
- a mission, every stage run and every analysis campaign are terminal;
- no selected mission has a processing or retryable failed inbox, or an active
  outbox delivery;
- every referenced catalogue dataset is already in the target organization or
  selected in the same adoption;
- all missions referencing a selected dataset are selected or already belong
  to the target;
- the target has no live dataset with the same name;
- the target organization storage policy can accept the logical resource size.

An unmanaged `input_dataset` prefix, malformed manifest, missing object,
checksum mismatch, cross-mission parent edge, target conflict or changed
source aborts the run before the database ownership switch.

Artifact Manifest v1 remains v1 under the copied mission prefix. Manifest v2
is rewritten as v3 with tenant-scoped CAS blobs and tenant-rewritten parent
edges. A v3 artifact attached to a legacy mission is rejected because its
ownership is already inconsistent and needs investigation.

## Operator workflow

Always begin with a dry run. Record its `run_id` and
`plan_checksum_sha256`:

```bash
python3 -m tools.adopt_legacy_storage \
  --target-organization-id acme \
  --owner-subject acme-admin \
  --actor-subject platform-operator \
  --dataset historical-images \
  --mission historical-flight
```

Run in a reviewed maintenance window with writes to the selected PostgreSQL
graphs and S3 prefixes stopped for the complete dry-run/apply sequence. Review
source/target prefixes, object counts, `target_write_bytes`, logical usage and
the selected resources. `target_write_bytes` is the conservative physical copy
volume; `logical_usage_bytes` is the separate organization quota impact. Apply
only the freshly recomputed same plan:

```bash
python3 -m tools.adopt_legacy_storage \
  --target-organization-id acme \
  --owner-subject acme-admin \
  --actor-subject platform-operator \
  --dataset historical-images \
  --mission historical-flight \
  --run-id 00000000-0000-0000-0000-000000000000 \
  --apply \
  --confirm-plan-checksum <reviewed-sha256>
```

`--all-legacy` is intentionally mutually exclusive with explicit resources
and should be reserved for a dedicated maintenance window after its larger
dry run has been reviewed.

## Recovery and audit

Copies carry the source key digest and source ETag in object metadata. A retry
reuses only a matching target; an unrelated existing object fails closed.
Tenant control objects carry their SHA-256 and are immutable by identity.

The organization usage ledger records:

- `legacy_adoption_started` with the plan checksum;
- one `legacy_adoption_resource` per committed dataset or mission;
- `legacy_adoption_completed`; or
- `legacy_adoption_failed` with the bounded exception type/message.

Reusing a completed `run_id` with the same plan is a no-op. Reusing it for a
different plan is rejected. A failure before commit may leave verified tenant
copies, which the next identical run safely reuses. If source identity or any
other checksum input changed, review a new plan with a new `run_id` instead. A
changed plan never overwrites control objects left by the failed plan: confirm
that no tenant row or completed event is bound, then perform an explicit
reviewed cleanup of only that failed run's target prefix before retrying.
PostgreSQL remains legacy-bound until the complete copy and revalidation phase
succeeds.

Migration `0033` extends the append-only usage action constraint. Its downgrade
is refused after any adoption audit event exists, because removing that schema
contract would make existing audit rows invalid.

## Deliberate limits

- Legacy sources are not garbage-collected by this command.
- Mission identifiers remain globally unique until all production legacy
  resources have been inventoried/adopted and every lookup migration is ready.
- The command uses the operator database and S3 credentials and must run only
  in a reviewed maintenance context, never in an API pod.
- Scientific datasets are not needed to qualify this control-plane workflow;
  its tests use synthetic bytes, real PostgreSQL and S3-compatible storage.
