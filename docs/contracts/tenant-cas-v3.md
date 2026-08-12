# Tenant content-addressed storage v3

## Invariant

New immutable blobs owned by an organization use this key:

`organizations/{organization_id}/blobs/sha256/{first-two}/{sha256}`

The organization is part of the object identity. Identical bytes published by
two organizations therefore produce two independent objects and cannot be
reported as cross-tenant reuse. Historical `legacy-unassigned` data retains
the global `blobs/sha256/...` layout.

Artifact Manifest v3 adds `organization_id` and requires every blob descriptor
to use that organization's CAS prefix. Workspace restore and map-product
resolution receive the durable mission organization and reject a v3 manifest
bound to another organization. Existing Manifest v1 and global v2 objects
remain readable during migration; new tenant writes use v3 whenever the
versioned manifest writer is enabled.

The same binding applies to immutable GCP calculation bundles and detection
shard results. Their new writers derive the organization from the durable
mission or stage run rather than trusting an object key supplied by the caller.
New tenant GCP requests reject global v1 descriptors. Workers may still read a
previously persisted v1 bundle, and detection finalization may still read an
existing global receipt, so an in-flight deployment does not require a bulk
rewrite. New publications always use tenant keys.

## Compatibility boundary

- v1 workspace manifests: readable, mission-prefix files;
- v2 workspace manifests: readable, historical global CAS;
- v3 workspace manifests: readable and writable, tenant CAS required;
- GCP bundle v1: legacy writer and migration-only worker read;
- GCP bundle v2: tenant writer and strict organization binding;
- historical global shard receipts: finalizer read only;
- new tenant shard receipts: tenant CAS required before insertion.

No scientific threshold, reconstruction setting, detector behavior or dataset
benchmark is changed by this contract.

## Verification

- canonical v3 round trips and malformed/cross-tenant descriptor rejection;
- identical-byte publication into two organizations without object reuse;
- successful same-tenant restore and denied cross-tenant restore;
- GCP bundle cross-tenant and global-key rejection at the API boundary;
- shard publication bound to the mission organization and override rejection;
- legacy v1/v2 reader compatibility.
