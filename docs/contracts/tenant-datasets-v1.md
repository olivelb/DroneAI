# Tenant dataset catalogue contract v1

## Source of truth

`datasets` is the authoritative catalogue for launchable mission inputs. An S3
prefix is not a dataset merely because objects exist below it. A catalogue row
records the immutable public ID, owner subject, storage prefix, manifest key,
file and image counts, total bytes and lifecycle state.

Direct multipart upload sessions remain the transient ingestion state. The API
creates the catalogue row only after every file is completed and the verified
`dataset-manifest.json` has been published. Only a `ready` dataset may be
listed, browsed or attached to a new mission. New missions store both the
compatibility `input_dataset` prefix and a foreign key to the catalogue row.
The legacy API-proxied `/datasets/upload` path returns `404` in every
environment so local development follows the same catalogue-producing flow.

Migration `0022` imports the newest completed upload session for each existing
dataset prefix and links existing missions when owner and prefix both match.
Raw S3 prefixes without a completed upload session are intentionally not
assigned to a tenant. They must be re-ingested or handled by a future explicit,
audited adoption workflow.

## Tenant boundary

Organization isolation is defined by the
[`organization-boundary-v1`](organization-boundary-v1.md) contract. Ordinary
accounts see only rows whose `organization_id` matches the authenticated
organization and whose `owner_subject` equals the authenticated principal
subject. Storage browse, preview and download endpoints authorize the requested
key before querying S3. An accepted key must be contained by either:

- a `ready` dataset owned by the selected tenant; or
- a mission owned by the selected tenant.

Other roots, unmanaged dataset prefixes and cross-owner keys return `404`.
Preview responses use private cache semantics.

Administrators default to their own subject. Cross-owner support within their
organization requires an explicit `owner_subject` parameter and emits a structured
`droneai.audit.dataset_access` warning. This follows the mission ownership
contract and prevents an administrator UI from silently becoming a global
tenant browser.

## Launch and deletion invariants

Mission creation locks and resolves the exact `ready` dataset prefix inside the
same database transaction that creates the mission. A nonexistent, incomplete,
deleted or foreign dataset is rejected before any stage or outbox event is
created.

Dataset deletion locks the catalogue row and refuses deletion while any mission
references it, including compatibility missions that predate the foreign key.
The row transitions through `deleting` before S3 mutation. A storage failure is
recorded as `deletion_failed` and may be retried; success leaves a `deleted`
tombstone that is excluded from ordinary access.

## v1 compatibility limits

- Historical S3 prefixes remain `datasets/{name}`. New explicit organizations
  write `organizations/{organization_id}/datasets/{name}`; migration `0024`
  does not copy or silently adopt historical objects.
- The browser upload currently accepts plain filenames rather than relative
  paths and records multipart ETags rather than full-file SHA-256 values.
- Recovery from a database commit failure immediately after an S3 multipart
  mutation requires the separate crash-recovery state-machine phase.
- Dataset deletion is administrator-only in v1. Self-service deletion needs a
  retention and billing policy before being exposed to operators.

These limits do not require scientific datasets to address. Their acceptance
tests use synthetic objects, injected storage/database failures and multiple
test principals.
