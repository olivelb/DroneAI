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

PostgreSQL is authoritative for normal members and credentials. The complete
lifecycle, hashing and bootstrap rules are defined in
[`identity-control-plane-v1.md`](identity-control-plane-v1.md).

Each transitional staging or production bootstrap entry in
`DRONEAI_API_KEYS_JSON` must contain:

- a random key of at least 32 characters;
- a non-empty `subject`;
- a cumulative `viewer`, `operator` or `admin` role;
- an explicit lower-case DNS-like `organization_id` of at most 64 characters.

Durable credentials store only a peppered digest and support organization-
scoped creation, rotation and revocation. The signed browser session carries
the durable member, credential and authorization-version references in addition
to the three public identity fields. The frontend
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

Migration `0026` adds PostgreSQL RLS across the complete organization-owned
table graph. The non-owner API role receives a transaction-local organization
identity; application predicates remain mandatory as the first layer. The
database-role, authentication and rollback rules are defined in
[`postgres-tenant-rls-v1.md`](postgres-tenant-rls-v1.md).

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

Every new mission, orthomosaic, image-tile, detection, status and control event
includes `organization_id`; dead-letter events preserve a valid organization
from their source record. The shared JSON Schema rejects unsafe path-like
values. Deterministic IDs, trace correlations and Kafka mission/tile keys all
include the organization, so equal mission and run identifiers in two tenants
cannot share idempotency or ordering state. Cancellation caches use the same
tenant-qualified identity.

Historical version-one events without an organization remain readable. A
tenant-bearing status event must match the durable mission organization before
database persistence or WebSocket fan-out; mismatches fail closed. Realtime
audience keys combine organization and subject, and API rate-limit keys do the
same. Stage scheduler capacity is counted per organization; executor Jobs
retain the member subject for attribution.

## Deliberate limits

- A Kubernetes Secret may still provide a transitional bootstrap key; normal
  members and hashed credentials are durable. Invitations, member recovery and
  a distinct metadata-only platform-support realm are implemented; OIDC and a
  management UI are not.
- Mission IDs remain globally unique in the database.
- Existing v1 storage is not automatically adopted into an explicit
  organization; such adoption needs an audited administrative workflow.
- Billing policy, retention and organization quota ledgers remain separate
  from scientific profiles and are governed by the SaaS policy contract.
