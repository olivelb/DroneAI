# Artifact Manifest v3 contract

Artifact Manifest v3 is the only supported workspace format for bounded Stage
Jobs. Every manifest and CAS blob belongs to a real organization. Workspace
v1, global v2 and global CAS fallback are retired; there is no replay or
automatic migration of old runs.

## Normalized model

A manifest contains an organization, changed logical files and immutable parent
references:

```json
{
  "schema_version": 3,
  "organization_id": "example-org",
  "files": [
    {
      "path": "models/final.ply",
      "role": "gaussian-model",
      "blob": {
        "key": "organizations/example-org/blobs/sha256/ab/abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "size": 123456,
        "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
      }
    }
  ],
  "parents": []
}
```

Digest-shaped values are illustrative. Parent descriptors have exactly
`artifact_id`, `manifest_key` and `checksum_sha256`; manifest keys end in
`/manifest.json`.

The parser rejects unsupported versions, absent/retired organizations,
unknown fields, duplicate files or parent IDs, invalid roles, noncanonical
paths, traversal, invalid sizes/digests and blob keys outside the declared
organization. Canonical serialization sorts files by logical path and parents
by artifact ID before sorting JSON keys. Current v3 canonical bytes are
unchanged by the cleanup.

## Publication and restoration

`shared.stage_workspace.publish_workspace` always publishes v3. It requires
`organization_id` and a default role. There is no writer-version switch.
Stage and product adapters pass the durable mission organization; CAS
publication also requires it and never falls back to global keys.

The writer resolves and checksum-verifies every parent. Unchanged files remain
inherited; changed files use conditional CAS publication. It rejects missing
inherited files unless the caller explicitly declares a partial workspace.
That mode preserves unmaterialized parent files; it cannot delete them.
Logical bytes and counts describe the full overlay. Reused bytes include
inherited files and existing same-organization CAS objects.

Restoration and map/viewer object resolution require
`expected_organization_id`. Every manifest in the parent graph must match it.
Parent cycles, conflicting sibling definitions, more than 64 manifests or
100,000 materialized files fail closed. Explicit selections must exist and
match at least one file. Every downloaded file is verified by size and SHA-256;
a corrupt download is removed. Cancellation hooks and symbolic-link rejection
during publication remain in place.

Detection-only selective restore remains a separate disabled-by-default
option. It materializes the declared orthomosaic while publishing a complete
parent overlay. Other stages restore their full input. Detection fan-out
still requires selective restore; removing the writer switch does not activate
either feature.

## Immutable CAS boundary

Keys use
`organizations/{organization_id}/blobs/sha256/{first-two}/{sha256}`.
Identical content in different organizations cannot count as shared reuse.
GCP calculation bundles retain their current tenant-bound **GCP schema v2**,
which is a different contract from workspace Manifest v3. GCP v1 and global
detection shard receipt replay are rejected.

Conditional single PUT, bounded multipart completion, retries, cancellation,
abort, concurrent-writer verification and the 5 TiB limit are unchanged.
An endpoint ignoring conditional completion must fail; no unconditional
fallback is permitted.

Qualify the actual provider before deploying this writer to it:

```bash
DRONEAI_QUALIFICATION_ORGANIZATION_ID=your-organization scripts/deploy/qualify-ovh-s3-multipart.sh
```

This forces a 6 MiB multipart probe under that organization, challenges the
conditional overwrite, checks original identity and reuse, then removes only
the probe. Local MinIO success does not qualify another provider.

## Release handling

Deploy matching API/control-worker and executor code together. Stop/drain old
work before switching: v1/v2 artifacts and global GCP/shard inputs are not
accepted by the new release. This cleanup does not delete existing objects,
change database rows or provide a storage migration.

See [tenant CAS](tenant-cas-v3.md) and the
[cleanup evidence](../audits/2026-08-28-current-production-cleanup.md).
