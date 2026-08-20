# Platform and scientific audit disposition — 2026-08-20

Audit base: `main@878c1d5`. The audit was rechecked against code rather than
accepted as a backlog verbatim. Platform correctness, external organization
controls and scientific qualification are tracked separately below.

## Corrected in this remediation

- Mission deletion and automatic retention now drain active `AIAnalysisRun`
  records as well as stage runs. New analysis creation/retry is rejected once
  deletion starts; finalization ownership is revoked, queued tile journals are
  retired, and cancellation is published transactionally through the outbox.
- A late analysis finalizer can no longer turn a durably cancelled run back
  into `failed`.
- Pre-authentication peer/public-credential rate limiting now covers every
  token-bearing HTTP request, while public readiness paths and credential-body
  routes keep their explicit handling.
- Migration `0035` refuses a downgrade before recreating either legacy global
  uniqueness constraint when tenant-scoped duplicate identities exist. The
  error directs operators to restore application/schema forward.
- New-mission quality-profile resolution is distinct from historical replay.
  Legacy profiles remain readable for replay, candidates require the explicit
  non-production candidate flag, and direct identifier posting cannot bypass
  the selectable catalog. Recipe identity is immutable on create; projected
  initialization and capacity-targeted growth overrides are qualification-only
  behind the same non-production gate; public expert overrides remain usable.
- Resident partition execution now rejects any missing planned core, even when
  the remaining cell count happens to satisfy the density minimum. Re-plan,
  merge or explicit failure must replace silent topology loss.
- Native-image footprint calculations now encode their undistorted-input
  invariant: the dense COLMAP workspace must expose `PINHOLE` or
  `SIMPLE_PINHOLE`, not a distorted camera model silently treated as pinhole.

## Confirmed, but not a local code-only change

- Protected overlays already require one distinct Secret per stage and a
  non-owner RLS role. Distinct Secrets are not proof of least capability:
  production provisioning must still grant stage-specific PostgreSQL and S3
  actions/prefixes, then exercise denial tests. This belongs to the OVH/IAM/DB
  provisioning evidence, not to scientific metrics.
- The API/control-worker role split exists, but the migration role and elected
  control role still require environment-specific grants and recovery drills.
- Trusted proxy topology and the selected OIDC issuer/claims contract require
  an actual ingress and identity provider. They cannot be qualified from the
  repository alone.

## Confirmed architectural work still open

These changes alter storage/accounting contracts and require their own R3
migration and real-engine qualification; they were not hidden inside this
guard-focused patch:

- reserve or preflight stage-output quota before physical S3 publication, with
  expiry, idempotency and orphan reconciliation;
- implement CAS mark/sweep garbage collection, legal hold and audit evidence;
- separate logical tenant usage from unique physical CAS bytes so overlays do
  not double count shared content;
- add durable dataset content SHA-256 without buffering multi-gigabyte browser
  files or assuming multipart ETags are hashes;
- define and migrate distinct database roles for schema migration and elected
  control work, then qualify least-privilege recovery on PostgreSQL.

## Scientific work requiring evidence or datasets

No organizational readiness statement closes these items:

- replace the V2 camera-centre convex hull with a union of calibrated camera
  footprints, version the evidence schema and recalibrate thresholds;
- qualify seam metrics with a valid mask, explicit units and dataset-backed
  thresholds;
- decide the GCP contract: keep the current robust Sim(3) adjustment and name
  it accurately, or implement true control-point bundle adjustment with
  checkpoints and covariance-aware acceptance;
- qualify current HEAD on full scenes for resident Normal/HQ, RTK/GCP,
  facade, fast preview and released profile governance;
- build labeled IA datasets and publish precision/recall/error-strata evidence;
- evaluate source-image projection for orthophoto texture as a scientific
  candidate, not as an organizational blocker.

The existing CPU control-plane E2E remains useful until OVH CPU/GPU pods are
available. It validates orchestration and durability, not photogrammetric or
inference quality. BIGZEN measurements remain scientific evidence only and do
not substitute for target-environment operational qualification.

## Compatibility contract for concurrent scientific branches

Unmerged benchmark/profile branches must preserve these new boundaries:

- add candidates to the candidate registry; do not make them public or bypass
  `quality_profile_for_new_mission`;
- keep historical replay IDs readable but never selectable for new missions;
- if partition planning can exclude a core, return explicit topology evidence
  and re-plan/merge/fail rather than returning a shorter anonymous list;
- load footprint cameras from the undistorted dense workspace or implement a
  calibrated distortion model and version the footprint evidence;
- do not weaken deletion/retention drain, token pre-auth limiting, or the
  distinction between CPU E2E and scientific qualification.
