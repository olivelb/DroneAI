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
4. Enable the v2 writer behind a disabled-by-default environment flag. **Done:**
   bounded stage adapters use `publish_workspace_v2` only when
   `DRONEAI_ARTIFACT_MANIFEST_V2_WRITE_ENABLED=true`; the Helm value
   `stageJobs.artifactManifestV2WriteEnabled` defaults to `false`.
5. Add a detection-only selective-restore canary. **Done behind a second
   disabled flag:** `DRONEAI_ARTIFACT_SELECTIVE_RESTORE_ENABLED=true` restores
   the exact orthomosaic path declared by the raster artifact and requires the
   v2 writer flag to be enabled.
6. Qualify complete products and rollback before enabling another target.

The restore engine resolves parents before children, so a child file replaces
an inherited file at the same logical path. Divergent definitions of one path
from sibling parents are rejected; identical definitions are deduplicated.
Resolution is bounded to 64 manifests and 100,000 logical files, and every
downloaded manifest and blob is checksum-verified. A selective request fails
closed if an explicit path is absent or if nothing matches. Legacy v1 files
have the synthetic role `legacy`, so their useful selective key is the exact
logical path.

The deployed stage adapters still request a full restore and publish v1 under
the default configuration. Existing v1 artifacts remain readable and no bulk
rewrite is planned. When explicitly enabled, each adapter records exact parent
manifests and assigns stable roles to its state, Gaussian model, raster and
detection products. Detection alone can additionally materialize only its
declared orthomosaic. Its child manifest remains a complete logical product:
the unmaterialized parent files stay inherited and only new detection files
are written to CAS. All other stages continue to materialize their full input.

The CAS publisher is intentionally not called by the v1 writer. Up to 5 GiB it
uses conditional `PutObject`; larger blobs use bounded multipart upload and
conditional `CompleteMultipartUpload`. Parts default to 64 MiB and grow when
needed to remain below 10,000 parts. Every failure or cancellation attempts an
abort, a concurrent 409/412 verifies the winning object, and the completed
object is always verified by size and SHA-256 metadata. An endpoint that does
not implement the conditional completion fails closed; there is no
unconditional fallback. The absolute object bound remains 5 TiB.

The v2 writer verifies and resolves every declared parent, inherits unchanged
files, and normally rejects a missing inherited path because manifest v2 has
no deletion tombstone. Its explicit partial-workspace mode treats absent local
files as inherited, never as deletions. Logical size and file count always
describe the fully resolved overlay; inherited and pre-existing CAS bytes
count as reused, while only newly transferred blob bytes and the new manifest
count as uploaded bytes. Both configuration switches are intentionally off in
every default environment, and Helm rejects selective restore unless the v2
writer is enabled. Provider
qualification must confirm conditional multipart completion before enabling
the writer for a stage capable of producing a blob above 5 GiB. AWS documents
the condition on
[`CompleteMultipartUpload`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CompleteMultipartUpload.html),
while the OVHcloud compatibility matrix confirms the multipart operations but
does not separately specify conditional headers.

Qualify the actual OVH endpoint with a random, automatically deleted 6 MiB
probe before changing the Helm flag:

```bash
scripts/deploy/qualify-ovh-s3-multipart.sh
```

The probe forces the production multipart code path, attempts a conditional
overwrite of the same key, verifies the original size and checksum metadata,
confirms CAS reuse, deletes the object and verifies its absence. It prints no
credentials. A passing local or mocked test is not a substitute for this
provider check.

The first real OVH GRA provider run passed on 10 August 2026; its non-sensitive
evidence is retained in
[`ovh-s3-conditional-multipart-2026-08-10.md`](../benchmarks/ovh-s3-conditional-multipart-2026-08-10.md).
