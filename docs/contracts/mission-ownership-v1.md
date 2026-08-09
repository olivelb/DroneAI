# Mission ownership and catalogue contract v1

## Boundary

Every mission is owned by the `subject` of the authenticated principal that
submits it. Migration `0013` adds the indexed, non-null `missions.owner_subject`
column. Existing rows are assigned to `legacy-unassigned`; they are not exposed
to ordinary accounts until an administrator explicitly adopts or inspects that
owner scope.

Mission identifiers remain globally unique. Ownership is checked independently
of the identifier so guessing a `vol_id` cannot expose state, logs, rasters,
vectors, features or AI analyses belonging to another subject.

## User workflows

- `/` is the mission launch studio.
- `/missions` is the authenticated subject's paginated catalogue.
- `/missions/{vol_id}` is the durable detail view. It exposes submitted
  parameters, attempts, phase snapshots, heartbeat age, persisted logs and
  published products.
- `GET /missions?limit=25&offset=0` returns the owner-scoped catalogue.
- `GET /missions/{vol_id}` returns one owner-scoped detail document.

The compatibility summary and state endpoints are owner-scoped as well. Live
WebSocket history and broadcasts are partitioned by owner on the server; the
browser never receives another tenant's event and does not filter secrets
client-side.

## Administrative support access

Administrators do not receive an implicit global view. Their default scope is
their own subject. Cross-tenant support access requires an explicit
`owner_subject` query parameter. Non-admin users receive `404` for such a
request, avoiding mission-existence disclosure. Accepted cross-tenant admin
access emits a structured `droneai.audit.mission_access` warning containing the
administrator subject, requested owner, action and mission identifier. This
logger must be collected by the deployment's immutable audit-log sink.

The same explicit scope is available on mission mutations and all `/maps`
metadata, tile, vector, feature, analysis and export routes.

## Compatibility and next phase

The `attempts` section currently projects the mission retry counter because the
legacy schema stores a single mutable lifecycle record. Phase 5 replaces this
projection with immutable `mission_stage_runs`, artifacts and parent edges
without changing the owner boundary introduced here.

## Validation performed

- 594 Python/CPU tests passed; the 13 skips require CuPy/CUDA and are unrelated
  to this API/frontend phase.
- Strict Ruff and mypy gates, shell checks, documentation links, event schemas,
  version contracts and Actions syntax passed.
- Frontend unit tests, ESLint and the Next.js production build passed.
- Nine Playwright scenarios passed, including catalogue-to-detail navigation.
- PostgreSQL/PostGIS migrations completed an `upgrade head`, `downgrade base`,
  then `upgrade head` round-trip and ended at revision `0013`.
- The duplication gate reports zero clones.
