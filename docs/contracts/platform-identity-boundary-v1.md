# Platform identity boundary v1

## Scope

Platform support is a durable identity realm separate from customer tenants.
It is an organizational SaaS control-plane facility; it does not grant access
to missions, datasets, maps, artifacts, scientific parameters, usage ledgers
or tenant members and credentials.

The only platform role is `support`. An organization `admin` is never promoted
implicitly into this realm, and a support identity is never accepted by a
tenant route, WebSocket audience or organization request quota.

## Durable support credentials

Platform members and credentials live in `platform_members` and
`platform_credentials`, without an `organization_id`. A credential token uses
the `dps.<uuid>.<secret>` format. PostgreSQL stores only the same peppered
HMAC-SHA-256 digest used by tenant credentials. The plaintext is returned once.

The operator database connection is the sole initial provisioning path:

```bash
python3 -m tools.manage_platform_support \
  --action provision \
  --subject support@example.com \
  --credential-name primary-workstation \
  --actor-subject platform-operator
```

The default is a read-only preview. Repeat with `--apply` only after reviewing
the subject and credential name. Capture the resulting token in the approved
secret manager; it cannot be reconstructed. Never pass a secret token on the
command line or store command output in the repository.

For emergency offboarding, preview then apply `--action suspend` without a
credential name. The transaction suspends the member, increments its session
authorization version and revokes every active credential. Reactivation uses
`--action reactivate --credential-name <new-name>` and always emits a new
one-time token; suspended credentials are never revived.

An authenticated support member may list, create, revoke and rotate only their
own credentials through `/platform/credentials`. Rotation creates the
replacement and revokes the previous credential atomically. Revocation,
expiry and member suspension invalidate both raw credentials and signed browser
sessions.

## Permitted platform operations

Support may:

- read organization identifiers, display names, state and timestamps;
- suspend or reactivate an organization;
- read the platform audit trail;
- manage only its own platform credentials.

Support may not rename an organization, inspect or mutate any tenant-owned row,
issue tenant invitations or recovery tokens, impersonate a member, or alter a
tenant SaaS policy. PostgreSQL RLS enforces the data-plane denial. A database
trigger additionally restricts platform organization updates to the status
field; the HTTP route is not the only enforcement layer.

Every platform lifecycle or organization-state mutation is written to
`platform_audit_events`. A database trigger rejects audit `UPDATE` and
`DELETE`, including owner-side mutation. Snapshots never include a plaintext
token or credential digest.

## Tenant invitations and recovery

Tenant invitations and recovery tokens use a separate one-time
`dic.<uuid>.<secret>` capability. Only its peppered digest is stored and its
maximum lifetime is 30 days.

- An organization `admin` creates, lists and revokes invitations for that
  organization through `/auth/invitations`.
- A durable active member creates, lists and revokes only their own recovery
  tokens through `/auth/recovery-tokens`.
- Issuance locks the owning organization or member, so concurrent requests
  cannot create duplicate live capabilities for the same subject and purpose.
- `POST /auth/capabilities/redeem` atomically consumes one capability and
  returns one new tenant credential. Replays, expiry and revocation fail closed.
- Recovery uses the member's current role and status; a capability cannot
  restore privileges removed after it was issued.

Capability redemption receives only the exact organization/member rows needed
to perform the transaction. It cannot see missions or another organization.
Creation, revocation and redemption are recorded in the tenant's immutable
identity audit.

## Deliberate limit: federation

OIDC federation is not implemented in this version. It requires a concrete
identity provider contract: trusted issuer, audience, JWKS rotation, subject
and organization claim mapping, role source, account-linking behavior and
break-glass policy. Until that contract is selected and qualified, durable
DroneAI credentials remain authoritative; deployments must not infer identity
or tenant membership from arbitrary proxy headers.
