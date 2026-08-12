# Identity control-plane contract v1

## Scope

This contract governs organization membership and API access. It is an
organizational SaaS control-plane requirement and is independent of scientific
datasets, reconstruction quality, detector accuracy, CUDA benchmarks and the
availability of OVH CPU/GPU test pods.

## Durable identity

PostgreSQL is authoritative for organizations, members and normal API
credentials. A member belongs to exactly one organization, has one cumulative
`viewer`, `operator` or `admin` role and is either `active` or `suspended`.
Role and status changes increment `auth_version`, invalidating derived browser
sessions. Suspending an organization or member also rejects every associated
credential.

An API credential has a public UUID and a random secret. The service stores
only an HMAC-SHA-256 digest keyed by `DRONEAI_CREDENTIAL_PEPPER`; the plaintext
token is returned once at creation or rotation. Rotation creates a replacement
and revokes the previous credential in one database transaction. Revocation,
expiry, member suspension and organization suspension invalidate both direct
API-key authentication and browser sessions derived from that credential.

Identity lifecycle events are organization-scoped and append-only. PostgreSQL
rejects `UPDATE` and `DELETE` against `identity_audit_events`. Audit snapshots
never contain a credential token or digest.

## Bootstrap and rollout

`DRONEAI_API_KEYS_JSON` is a transitional bootstrap registry, not the durable
credential store. A configured static organization admin calls:

1. `POST /auth/bootstrap` to create or adopt the organization and first member;
2. `POST /auth/credentials` to issue a durable credential;
3. `POST /auth/session` with that credential to verify browser access;
4. removal of `api-keys.json` from the Kubernetes Secret after every required
   administrator has a tested durable credential. Existing browser sessions
   derived from a removed bootstrap entry are rejected immediately.

The bootstrap endpoint is idempotent. Production requires database
authentication, a session signing secret and a distinct credential pepper, all
at least 32 characters. The pepper is not a credential and must not be exposed
to clients. Changing it invalidates all durable credentials, so pepper rotation
requires a planned credential re-issue.

Keep the bootstrap entry until the migrated API and durable admin credential
have passed the deployment smoke test. Downgrading below migration `0025`
drops durable members, credential digests and identity audit records; after
cutover, recover by rolling forward. If an old application image must be
restored, first provision a new static break-glass key because durable plaintext
credentials cannot and must not be reconstructed from their digests.

Static bootstrap authentication is disabled by default in production through
`DRONEAI_ALLOW_STATIC_BOOTSTRAP=false`. Initial adoption may temporarily set it
to `true`; `/ready` then reports `bootstrap_credentials_active: true` without
exposing credential material. After a durable admin credential is tested,
remove `api-keys.json` and restore the flag to `false`. A production process
fails at startup if a static registry is still present while the flag is off.

## Authorization invariants

- Member and credential queries always include `organization_id`.
- A non-admin may list, create, rotate and revoke only their own credentials.
- An organization admin may manage members and credentials only inside their
  organization.
- The final active organization admin cannot be demoted or suspended.
- Credential lookup outside the caller's organization returns `404`.
- No tenant role is platform-global. The separate durable support realm must
  not be inferred from organization `admin` and has no tenant data access.

Organization admins issue one-time invitations; durable active members issue
only their own recovery capabilities. Tokens are shown once, stored as
peppered digests, expire within 30 days and are consumed atomically with the
replacement credential. The complete cross-realm boundary is defined in
[`platform-identity-boundary-v1.md`](platform-identity-boundary-v1.md).

## Deliberate limits

This version does not provide OIDC federation or a management UI. PostgreSQL
row-level security provides a second identity/tenant enforcement layer under
[`postgres-tenant-rls-v1.md`](postgres-tenant-rls-v1.md); application queries
and foreign keys remain mandatory.
