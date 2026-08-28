# Tenant content-addressed storage v3

## Invariant

Every immutable blob uses
`organizations/{organization_id}/blobs/sha256/{first-two}/{sha256}`.
The organization is mandatory and cannot be `legacy-unassigned`. Identical
bytes in different organizations produce separate objects and cannot count
as cross-organization reuse.

[Artifact Manifest v3](artifact-manifest-v3.md) is the only workspace format.
Restore and map/viewer resolution require the durable mission organization
and reject a mismatching root or parent manifest. The writer is unconditional;
there is no version rollout flag.

GCP calculation bundles use their own current schema v2 with the same tenant
binding. Shard publication resolves the durable mission organization and
checks any supplied override. Finalization accepts only matching tenant CAS
receipts.

## Supported contracts

| Contract | Current behavior |
|---|---|
| Workspace Manifest v3 | Read/write; organization and tenant CAS required |
| Workspace v1 and global v2 | Rejected |
| GCP bundle v2 | Read/write; organization binding required |
| GCP bundle v1 | Rejected, including replay |
| Tenant shard receipt | Verified against durable mission organization |
| Global shard receipt | Rejected, including finalization |

No scientific thresholds, reconstruction parameters or detector behavior
change. Existing objects and database rows are untouched, but old runs are
not replayable through the new code. The deployment must not mix old and new
artifact contracts.

## Verification

- canonical v3 byte parity and malformed/version rejection;
- same-content publication into different organizations without shared reuse;
- same-tenant full/selective restore and denied cross-tenant parent graphs;
- strict GCP and shard validation, with no global fallback;
- retained conditional multipart, cancellation, concurrency, checksum and
  partial-overlay safeguards;
- local MinIO integration, separately from provider-specific qualification.
