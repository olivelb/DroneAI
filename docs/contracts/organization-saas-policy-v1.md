# Organization SaaS policy and usage ledger v1

## Boundary

This contract defines commercial capacity and lifecycle controls. It never
reads or modifies reconstruction recipes, Gaussian limits, detector settings,
GCP thresholds, quality profiles or dataset-backed scientific gates.

One optional `organization_saas_policies` row configures:

- logical storage bytes;
- concurrent logical stage runs;
- authenticated API requests per minute and burst;
- terminal-mission retention days.

A null field is deliberately unlimited/disabled. Request rate and burst are
both null or both positive. The platform scheduler envelope remains an
independent upper bound: an organization policy can reduce it, never increase
cluster capacity.

Tenant `admin` is not a billing/platform role and cannot mutate this row over
HTTP. Provision a complete policy through the operator database with the
read-only-by-default command:

```bash
python3 -m tools.manage_organization_policy \
  --organization-id acme-survey \
  --actor-subject platform-support@example.com \
  --policy-file /secure/path/acme-policy.json
```

Review the printed before/after document, then repeat with `--apply`. The JSON
must contain all five keys, using a positive integer or `null` for each:

```json
{
  "concurrent_stage_runs_limit": 2,
  "request_burst": 120,
  "request_rate_per_minute": 600,
  "retention_days": 30,
  "storage_limit_bytes": 536870912000
}
```

Every applied change increments `version` and writes the before/after values to
the append-only usage ledger.

## Storage accounting and enforcement

Billable logical storage is the sum of:

- non-deleted dataset catalogue bytes;
- active or failed multipart reservations that may still own objects;
- known `MissionArtifact.size_bytes` values.

Upload creation locks the policy row, calculates current usage and reserves the
whole declared batch before issuing any S3 URL. Concurrent replicas therefore
cannot both consume the same remaining allowance. An idempotent
`storage_reserved` event is committed with the upload intent. Successful abort,
expiry cleanup and dataset deletion write `storage_released` only after object
cleanup has succeeded.

Stage output sizes are post-accounted because their size is not known before
compute. Missing artifact sizes count as zero and are an observability defect;
they never justify treating an unverified S3 inventory as exact billing. A
future physical-byte inventory may replace this logical v1 definition through
a versioned contract.

## Compute and request enforcement

The stage scheduler groups modern work by `organization_id`. It clips each
organization's active logical run count to the lower of the platform limit and
the commercial limit. Physical fan-out units remain separately bounded by the
global/resource scheduler envelopes. Every dispatch attempt writes a
`stage_scheduled` event with stage, resource class and physical units.

Protected Helm overlays enable an organization-wide PostgreSQL token bucket for
authenticated HTTP requests. It is shared by all API replicas and identities
inside the organization. Health and CORS preflight requests are excluded;
unauthenticated requests still reach normal authentication handling. Throttled
responses return `429`, `Retry-After`, `X-RateLimit-Scope: organization` and the
configured limit. Audit events are coalesced to one per organization/minute so
abusive traffic cannot amplify the ledger without bound.

## Retention

Only terminal missions (`success`, `completed`, `error`, `cancelled`, `stale`)
older than the configured number of days are eligible. The elected control
worker claims them with row locks, marks them `deleting`, deletes the exact
durable mission prefix, then deletes the database graph and writes
`retention_deleted`. Object-store failure produces durable `deletion_failed`
state and `retention_failed`; retry is delayed by the configured backoff. A
cleanup already claimed remains retryable even if the policy is later disabled,
so a crash cannot strand a partially deleted mission graph.

Retention is disabled for organizations without a positive policy. Dataset
inputs are not automatically deleted with a mission because they may be shared
by several missions; dataset deletion remains an explicit admin operation.
Tenant CAS garbage collection and legal hold are outside v1 and must be added
before policies requiring either behavior are sold.

## Isolation and audit

Migration `0028` applies PostgreSQL RLS to the policy, request bucket and ledger.
Tenant reads are organization-scoped. A PostgreSQL trigger rejects every
`UPDATE` or `DELETE` against `organization_usage_events`, including attempts by
the operator table owner. Tenant admins may inspect their policy, live usage and
ledger at:

- `GET /operations/organization/capacity`;
- `GET /operations/organization/usage-events`.

The ledger records operational usage and decisions, not invoices. Pricing,
tax, legal hold, refunds and payment-provider reconciliation require a separate
billing contract.
