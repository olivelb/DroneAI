# Organization boundary contract v1

## Organizational scope, not scientific scope

An organization is the SaaS isolation boundary. It controls who may see or
mutate missions, datasets, upload sessions, realtime updates and storage keys.
It does not define reconstruction quality, Gaussian capacity, geospatial
accuracy, detector quality or any acceptance threshold requiring a scientific
dataset. Those remain independent qualification gates.

`subject` identifies a human or service for attribution and member-level
authorization. `organization_id` identifies the customer boundary. Reusing the
same subject string in two organizations must never grant cross-organization
access.

## Authentication contract

Each staging or production entry in `DRONEAI_API_KEYS_JSON` must contain:

- a random key of at least 32 characters;
- a non-empty `subject`;
- a cumulative `viewer`, `operator` or `admin` role;
- an explicit lower-case DNS-like `organization_id` of at most 64 characters.

The signed browser session carries all three identity fields. The frontend
rejects a session response without `organization_id` instead of silently
falling back to a global tenant. Development with authentication disabled uses
the fixed `local-development` organization.

An administrator may explicitly support another owner only inside the
administrator's organization. The `admin` role is not a platform-global role.

## Persistence and query invariant

Migration `0024` adds a non-null, indexed `organization_id` to missions,
datasets and upload sessions. Every authenticated read and mutation filters by
organization before applying the existing owner/member rule. Dataset name
reservations and active multipart uploads are unique per organization.

Historical resources use `legacy-unassigned`; historical local-development
rows use `local-development`. Scheduler fairness falls back to the historical
owner for legacy rows, while new rows share concurrency quotas at organization
level.

## Versioned storage layout

New organization-scoped writes use:

```text
organizations/{organization_id}/datasets/{dataset_name}/...
organizations/{organization_id}/missions/{vol_id}/...
```

Existing `datasets/{name}` and `missions/{vol_id}` objects remain readable and
are not copied implicitly. Authorization resolves storage access through the
database catalogue, so both layouts use the same organization and owner checks.
Multipart recovery derives its prefix from durable file intent and therefore
continues safely across the v1-to-v2 transition.

## Cross-process propagation

Mission events include `organization_id`, and their JSON Schema rejects unsafe
path-like values. Legacy events remain readable. Realtime audience keys combine
organization and subject, and API rate-limit keys do the same. Stage scheduler
capacity is counted per organization; executor Jobs retain the member subject
for attribution.

## Deliberate limits

- API keys still come from a Kubernetes Secret; self-service membership,
  hashed credentials, rotation UI and revocation records are not implemented.
- PostgreSQL row-level security is not yet a second enforcement layer; the API
  query boundary is authoritative in v1.
- Mission IDs remain globally unique in the database.
- Existing v1 storage is not automatically adopted into an explicit
  organization; such adoption needs an audited administrative workflow.
- Billing, retention and organization quota ledgers are separate future
  organization features and must not be inferred from scientific profiles.
