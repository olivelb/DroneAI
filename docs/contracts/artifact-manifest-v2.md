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
3. Add parent-overlay resolution and selective materialization. **Done for the
   shared restore engine:** every parent checksum is verified recursively and
   callers can select exact logical paths, roles, or their union.
4. Enable the v2 writer behind a disabled-by-default environment flag. **Writer
   engine done:** `publish_workspace_v2` publishes only new or changed CAS
   blobs and a delta manifest, but no stage adapter calls it yet.
5. Qualify complete products and rollback before enabling another target.

The restore engine resolves parents before children, so a child file replaces
an inherited file at the same logical path. Divergent definitions of one path
from sibling parents are rejected; identical definitions are deduplicated.
Resolution is bounded to 64 manifests and 100,000 logical files, and every
downloaded manifest and blob is checksum-verified. A selective request fails
closed if an explicit path is absent or if nothing matches. Legacy v1 files
have the synthetic role `legacy`, so their useful selective key is the exact
logical path.

The deployed stage adapters still request a full restore and the writer still
publishes v1. Consequently, this engine capability does not change production
I/O yet. Existing v1 artifacts remain readable and no bulk rewrite is planned;
role mappings and selective requests will be activated per adapter before the
v2 writer rollout.

The current CAS publisher is intentionally not called by the v1 writer. It
uses a conditional single-part `PutObject` and fails closed above the S3 5 GiB
single-object request limit. Multipart CAS publication for larger files must
land before the v2 writer can accept such an artifact; silently falling back
to a non-conditional overwrite is forbidden.

The v2 writer verifies and resolves every declared parent, inherits unchanged
files, and rejects a missing inherited path because manifest v2 has no deletion
tombstone. Existing CAS blobs count as reused bytes; only newly transferred
blob bytes and the new manifest count as uploaded bytes. This API is currently
an inactive rollout primitive, not a production configuration switch.
