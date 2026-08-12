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

Migrations and control workers use the operator connection because they
perform schema changes or intentionally reconcile work across organizations.
Bounded compute Jobs are not control workers: they are tenant-bound and must
also use RLS. Helm therefore separates three credential classes:

- `database-url`: migration and cross-tenant control-worker role;
- `api-database-url`: non-owner dashboard API role.
- `stage-database-url`: non-owner bounded-executor role, supplied in each
  stage-specific Secret.

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
event. New status events carry that organization. The consumer calls
`droneai_mission_audience(vol_id)`, a stable `SECURITY DEFINER` function with a
fixed search path that returns only organization and owner, and rejects the
event if the returned organization differs. Persistence then runs in the
resulting tenant transaction. Historical version-one events without an
organization use the same narrow lookup for compatibility. The request-serving
container never receives the operator database URL for this path.

The dashboard API schema-wait init container also uses `api-database-url`; no
container in the request-serving Pod receives the operator URL.

## Bounded executor transaction context

The control worker injects `DRONEAI_ORGANIZATION_ID`, the durable mission ID,
`vol_id`, owner and `Mission.workspace_prefix` into every Job. The executor
opens every claim, heartbeat, cancellation, shard-receipt and publication
transaction with that organization. It rejects any mismatch between the Job
binding and the durable mission, and in protected environments verifies that
PostgreSQL reports RLS active for the stage graph. An owner, superuser or
`BYPASSRLS` credential therefore fails before scientific work starts.

Provision each stage role as `NOSUPERUSER`, `NOBYPASSRLS`, non-owner. Grant
only `SELECT` on `missions`; `SELECT, UPDATE` on `mission_stage_runs`;
`SELECT, INSERT` on `mission_artifacts` and `mission_artifact_parents`; and the
corresponding sequences. Detection additionally needs `SELECT, INSERT` on
`detection_shard_receipts` and its sequence. RLS supplies the tenant boundary;
the distinct stage roles keep compromise blast radius explicit.

## Protected graph

Migration `0026` enables one fail-closed policy on:

- organizations, members, API credentials and identity audit;
- dataset upload sessions/files and datasets;
- missions, logs, stage runs, shard receipts and artifacts/parents;
- analyses/tiles, detections, processed tiles and map features/audit;
- GCP sets, points, observations and audit, plus raster styles;
- tenant-attributed outbox records;
- organization SaaS policies, shared request buckets and the append-only usage
  ledger added by migration `0028`.

Child policies resolve ownership through their protected parent. Artifact
lineage requires both child and parent artifacts to be visible. The inbox and
rate-limit tables remain infrastructure state: they are not exposed by tenant
routes, while the inbox consumer still needs cross-organization idempotency.

The usage ledger additionally rejects `UPDATE` and `DELETE` through a database
trigger, including table-owner mutations. The table owner is intentionally not
forced through RLS so migrations and
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
- a tenant-bound executor can claim and update its own run but cannot read or
  update another organization run or publish an artifact for it;
- the operator role still performs cross-organization worker work.

These checks complement, rather than replace, HTTP authorization tests and the
current E2E operator metric used until OVH CPU/GPU pods are available.
