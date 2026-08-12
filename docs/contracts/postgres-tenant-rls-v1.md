# PostgreSQL tenant RLS contract v1

## Scope and separation from science

PostgreSQL row-level security is a defense-in-depth boundary for SaaS data. It
does not evaluate reconstruction quality, Gaussian capacity, map accuracy,
detection quality or any dataset-backed scientific threshold. Its qualification
uses synthetic organizations and rows only.

Application ownership filters remain mandatory. RLS is the independent second
layer that turns a missed organization predicate into an empty read or a
rejected write.

## Database-role boundary

The request-serving dashboard API must connect as a PostgreSQL role that is:

- not the owner of any application table;
- `NOSUPERUSER` and `NOBYPASSRLS`;
- limited to `CONNECT`, schema `USAGE`, table DML and sequence use.

Migrations and background workers use the operator connection because they
perform schema changes or intentionally reconcile work across organizations.
Helm therefore reads two URLs from the storage Secret:

- `database-url`: migration and worker role;
- `api-database-url`: non-owner dashboard API role.

Staging and production rendering fails if both workloads reference the same
Secret key. API readiness also evaluates
`row_security_active('missions'::regclass)` and fails closed when the runtime
role bypasses RLS.

Provision the API role before rollout using the database provider or an
audited administrator session. Run the grants as the same role that owns the
Alembic-created objects, replacing `droneai_api` as needed:

```sql
CREATE ROLE droneai_api LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOINHERIT NOBYPASSRLS;
-- Set or rotate its password through the provider/secret workflow; do not
-- place the password in a checked-in SQL file or shell history.
GRANT CONNECT ON DATABASE droneai TO droneai_api;
GRANT USAGE ON SCHEMA public TO droneai_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
  TO droneai_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO droneai_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO droneai_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO droneai_api;
```

The default privileges must be owned by the same migration role that creates
future tables. Reapply the current-object grants after migration `0026` when
adopting RLS on an existing database.

## Transaction context

Every authenticated HTTP router binds its validated organization to a Python
request context. `shared.database.get_session()` copies it into PostgreSQL with
transaction-local `set_config('droneai.organization_id', ..., true)`. The value
is cleared automatically at commit or rollback and therefore cannot leak when
SQLAlchemy returns a connection to the pool. An unbound API-role transaction is
fail closed for tenant tables.

Database-credential authentication happens before the organization is known.
That transaction sets only the public credential UUID in
`droneai.authentication_credential_id`. Policies expose that credential and
its member and organization long enough to validate the peppered digest,
status, expiry and authorization version. It does not expose missions,
datasets, outbox records or another credential.

The realtime Kafka consumer needs an organization before applying one status
event. It calls `droneai_mission_audience(vol_id)`, a stable `SECURITY DEFINER`
function with a fixed search path that returns only organization and owner.
Persistence then runs in the resulting tenant transaction. The request-serving
container never receives the operator database URL for this path.

## Protected graph

Migration `0026` enables one fail-closed policy on:

- organizations, members, API credentials and identity audit;
- dataset upload sessions/files and datasets;
- missions, logs, stage runs, shard receipts and artifacts/parents;
- analyses/tiles, detections, processed tiles and map features/audit;
- GCP sets, points, observations and audit, plus raster styles;
- tenant-attributed outbox records.

Child policies resolve ownership through their protected parent. Artifact
lineage requires both child and parent artifacts to be visible. The inbox and
rate-limit tables remain infrastructure state: they are not exposed by tenant
routes, while the inbox consumer still needs cross-organization idempotency.

The table owner is intentionally not forced through RLS so migrations and
system workers can operate across tenants. This is why the distinct non-owner
API role is a release invariant, not an optional hardening suggestion.

## Migration, rollback and qualification

Existing outbox rows are backfilled from their mission, then their event
organization, then `legacy-unassigned`. Downgrade removes policies, the audience
function and the outbox organization column; re-upgrade recreates them.

The PostgreSQL CI test creates a real temporary non-owner `NOBYPASSRLS` role and
proves:

- no context returns no tenant rows;
- organization A cannot read or insert organization B data;
- descendant and outbox policies follow the root organization;
- authentication sees only the nominated credential identity;
- transaction context does not survive pool reuse;
- the realtime audience function does not unlock tenant rows;
- the operator role still performs cross-organization worker work.

These checks complement, rather than replace, HTTP authorization tests and the
current E2E operator metric used until OVH CPU/GPU pods are available.
