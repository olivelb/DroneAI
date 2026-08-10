# Artifact Manifest v2 contract

Artifact Manifest v2 is the incremental storage contract for bounded stage
Jobs. The deployed writer still emits workspace manifest v1. The current
reader accepts both versions so reader compatibility is available before any
v2 object is published.

## Normalized model

A v2 manifest contains logical files and immutable parent references:

```json
{
  "schema_version": 2,
  "files": [
    {
      "path": "models/final.ply",
      "role": "gaussian-model",
      "blob": {
        "key": "blobs/sha256/ab/abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789",
        "size": 123456,
        "sha256": "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
      }
    }
  ],
  "parents": [
    {
      "artifact_id": "8ff04030-30b0-4d3b-9404-53aff90f730e",
      "manifest_key": "missions/example/stage-runs/parent/manifest.json",
      "checksum_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

The example digest-shaped values illustrate the format; they are not retained
objects.

Rules enforced by `shared.artifact_manifest`:

- logical paths and object keys are canonical relative POSIX paths;
- absolute paths, traversal, backslashes, URI forms and duplicate logical
  paths are rejected;
- roles use lower-case stable identifiers;
- every blob key is exactly `blobs/sha256/<first-two>/<sha256>`;
- size is a non-negative integer and every digest is lower-case SHA-256;
- parent artifact IDs are unique and parent manifest keys end in
  `/manifest.json`;
- supplied manifest graphs are rejected when their parent edges contain a
  cycle;
- canonical serialization sorts files by logical path and parents by artifact
  ID before using sorted compact JSON.

## Compatibility and rollout

The rollout is deliberately asymmetric:

1. Deploy the v1/v2 reader while continuing to publish v1.
2. Add idempotent CAS blob publication and its concurrency tests. **Done:**
   `publish_content_addressed_file` uses `If-None-Match: *`, reuses a verified
   existing blob and verifies the winner of a concurrent 409/412 race.
3. Add parent-overlay resolution and selective materialization.
4. Enable the v2 writer behind a disabled-by-default environment flag.
5. Qualify complete products and rollback before enabling another target.

The current reader materializes every file explicitly listed by either
manifest. It records the restored schema version in `workspace_transfer`
provenance. It does not yet resolve inherited parent files; therefore manually
publishing a delta-only v2 manifest is unsupported until step 3. Existing v1
artifacts remain readable and no bulk rewrite is planned.

The current CAS publisher is intentionally not called by the v1 writer. It
uses a conditional single-part `PutObject` and fails closed above the S3 5 GiB
single-object request limit. Multipart CAS publication for larger files must
land before the v2 writer can accept such an artifact; silently falling back
to a non-conditional overwrite is forbidden.
