# Database rate-limit scope isolation

Migration 0039 isolates `tile`, `identity:peer` and `identity:credential` with a
composite primary key and a `(scope, updated_at)` index. Apply the migration
with API traffic paused, then restart all API and control-worker replicas;
mixed old/new writers are not supported because the old limiter performs global eviction.

Existing hashes cannot be reversed into scopes. They remain in `legacy` and are
adopted on first use without resetting tokens. Unused legacy rows are retained;
they must not be purged without a separately verified expiry policy.

The supervised control worker reclaims at most 256 fully refilled idle buckets
per scope every 30 seconds. HTTP `consume()` never counts or deletes buckets.
MAX_CLIENTS is a database retention target, not a hard admission limit: active
quotas can exceed it under high cardinality, as discarding active quotas resets
security limits. Observe table growth and tune worker capacity/upstream ingress
limits for the deployment. Local in-memory limiter behavior is unchanged.

Rollback: stop new writers, downgrade to 0038, then restart old binaries.
Downgrade preserves all buckets and fails transactionally if different custom
scopes reused a key hash; resolve those collisions explicitly before rollback.
