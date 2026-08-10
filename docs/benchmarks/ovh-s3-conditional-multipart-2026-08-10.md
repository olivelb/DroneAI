# OVH S3 conditional multipart qualification — 10 August 2026

## Scope

- Candidate commit: `3065daff93cd8f6763c8d6a4bb85bf0c4e67e9eb`
- Endpoint: `https://s3.gra.io.cloud.ovh.net`
- Bucket: `droneai-preprod-fe7dc125`
- Probe: random 6 MiB object
- Started from the authoritative WSL checkout with:

  ```bash
  scripts/deploy/qualify-ovh-s3-multipart.sh
  ```

The wrapper read scoped application credentials from the protected Terraform
state path without printing them. No credential or state content is retained
in this report.

## Result

The qualification completed at `2026-08-10T08:19:55Z` with:

```json
{
  "cleanup_verified": true,
  "conditional_conflict_code": "PreconditionFailed",
  "size_bytes": 6291456,
  "status": "passed"
}
```

Observed gates:

- the production multipart CAS path completed successfully;
- a second multipart completion on the existing key with
  `If-None-Match: *` was rejected with `PreconditionFailed`;
- the original object size and SHA-256 metadata remained unchanged;
- a second normal CAS publication reused the verified object without transfer;
- the probe object was deleted and its absence was verified.

This qualifies conditional multipart completion on the current OVH GRA
endpoint. It does not enable Artifact Manifest v2 by itself; the Helm rollout
flag remains disabled by default and still requires a bounded stage rollout
and rollback evidence.
