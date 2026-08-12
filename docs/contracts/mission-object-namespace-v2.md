# Mission object namespace v2

## Invariant

Every mission owns exactly one durable object-store root:

- organization mission: `organizations/{organization_id}/missions/{vol_id}`;
- historical `legacy-unassigned` mission: `missions/{vol_id}`.

`missions.workspace_prefix` stores that root. Migration `0027` fills missing
roots from the persisted organization and mission identifiers, then makes the
column non-nullable. Runtime code validates the stored value against the same
canonical rule and fails closed if the binding has been changed or corrupted.

All mission-owned keys are derived through `MissionObjectNamespace`. This
includes stage workspaces and manifests, GCP diagnostics and camera metadata,
COLMAP products, orthomosaic tiles, AI tile results, analysis vectors, API map
fallbacks, frontend browsing/downloads and mission deletion. A structural test
rejects newly reconstructed `missions/{vol_id}` paths in production sources.

## Event boundary

New mission, orthomosaic, tile and tile-result events carry both
`organization_id` and `workspace_prefix`. Consumers validate that binding
before reading or writing objects and propagate it to downstream events.

Events that contain neither field retain v1 compatibility and are interpreted
only as `legacy-unassigned`. An event declaring a non-legacy organization
without its durable workspace prefix is rejected.

## Deletion and recovery

Mission deletion captures and validates the persisted root while the mission
row is locked, marks the mission as deleting, and deletes only that exact
prefix. Manual stage artifact recovery accepts only the manifest path derived
from the same root, run and stage.

## Compatibility boundary

This contract preserves v1 legacy mission roots and Artifact Manifest v1/v2
reads. New organization-owned CAS writes are now isolated by
[`Tenant content-addressed storage v3`](tenant-cas-v3.md); old global manifests
remain readable during rollout.

## Verification

- PostgreSQL upgrade from `0026`, null-root backfill, `NOT NULL` rejection,
  full downgrade to base and re-upgrade to head;
- real PostgreSQL/Kafka/MinIO composition tests;
- tenant and legacy key unit tests plus the production source guard;
- Python strict typing/static gates and frontend lint/build.
