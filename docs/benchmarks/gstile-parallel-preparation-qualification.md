# GSTile parallel pack preparation

2026-08-27. Implemented behind explicit `--pack-workers 2|4`; default remains
one synchronous worker. Complete-build timings pending.

## Contract and bounds

The traversal copies each tile's PLY fields and source IDs into read-only owned
arrays after queue admission. Workers encode canonical Q96; for individual
packs they also validate CRC, hash and prepare the Zstd sidecar. A single caller
consumes results in traversal order and performs all atomic writes/fsyncs.
For aggregate packs, tile encoding is parallel but assembly and compression
remain synchronous. V4 proxy generation and spatial partitioning are unchanged.

`--pack-pending-bytes` defaults to 134,217,728 (128 MiB), accepts 1 MiB–1 GiB,
and reserves owned input bytes plus twice the canonical output size. At most
`pack_workers` jobs may be submitted. This bounds queued/active input and
retained output reservations, **not total RSS**, Python metadata, NumPy scratch,
or a transient incompressible Zstd buffer. Existing aggregate payload storage
has its separate 256 MiB bound. A single oversized tile drains the queue and
runs synchronously instead of rejecting a previously supported leaf.

All workers are joined before bundle cleanup, including traversal failures.
Cancellation is checked during admission, every 50 ms while waiting, before
commit and before manifest publication. Running native preparation is not
preempted; it finishes without filesystem effects. No result is committed after
cancellation is observed. Only `published` denotes a complete durable bundle;
per-tile traversal progress can precede deferred writing. Ordered `pack_written`
events for aggregates and canonical pack ordering are retained.

The `pack_preparation` progress event records configured workers, queue byte
limit, peak reservations/tasks and oversized inline count. Execution policy is
not stored in the immutable manifest, so changing worker count does not change
bundle identity. Rollback: omit `--pack-workers` or use 1. No renderer, dataset,
format, cache or deployment migration is needed.

## Verification

18 new tests fail before implementation. **70 affected tiler/probe tests pass**,
including whole-directory byte equality across serial/2/4-worker builds for
leaf-only and adaptive V4, individual and aggregate packs. Tests cover byte and
task backpressure, oversized fallback, out-of-order completion, writer-thread
ownership, cancellation/join, worker errors (including a task's TimeoutError),
disk failure, non-publication/cleanup, source preservation and CLI options.
Scoped Ruff and diff checks pass. No new visual comparison is required for
identical canonical files; this does not assert geometric accuracy beyond the
source or extend qualification to untested datasets.

## Predeclared full-build pilot

Reference: `0ff7ab0531c017c8e84219f5e205ff572ed11087` in a retained detached
worktree. Candidate: the implementation commit pinned by the driver. Same
Python interpreter, local Ubuntu WSL2 / i9-13900H, filesystem and input PLY.
Fixture: the existing deterministic generator, 1,048,576 SH3/directional records.
It is synthetic and has correlated coefficients; no claim about a full 50 M
Saint-Etienne build follows from this pilot.

Two profiles: leaf-only individual packs; adaptive V4 with 16,384 proxies and
2 MiB aggregation. Both retain 65,536 leaves and 131,072 read chunks. Four arms:
old serial implementation, new synchronous 1, parallel 2 and parallel 4.
Each has a warmup, then three measured orders: old/1/2/4, 4/2/1/old, 1/old/4/2.
All runs and outputs are retained; no OS cache flush and no sample exclusion.
End-to-end wall time includes partitioning, preparation, durable writes and
publication; fixture generation and post-build hashing are excluded.

The driver uses one fresh child per build, preserves stdout/stderr and reports,
and checks every output file hash and the bundle ID against the old arm.
For acceptance, inspect medians and each paired observation, preserve negative
results, and do not generalize the default. A gain in codec throughput alone
does not qualify integration. The earlier
[codec-only probe](gstile-pack-worker-probe.md) is separate evidence.
