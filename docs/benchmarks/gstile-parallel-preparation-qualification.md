# GSTile parallel pack preparation

2026-08-27–28. Implemented behind explicit `--pack-workers 2|4`; default remains
one synchronous worker. Full-build pilot and its limitations are recorded below.

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
worktree. Runtime: `1872c59418867eacbc8274faf81b784263483af0`; clean candidate
with benchmark driver: `aaa36fe5d1bbf32401eb7ecbc1a5e023071caf79`. Same
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
and checks canonical pack/sidecar/manifest hashes against the old arm. An
independent post-cohort verifier additionally checks the complete file list
(no omitted/unexpected files), all file hashes and clean report provenance.
For acceptance, inspect medians and each paired observation, preserve negative
results, and do not generalize the default. A gain in codec throughput alone
does not qualify integration. The earlier
[codec-only probe](gstile-pack-worker-probe.md) is separate evidence.

At these sizes, each V4 tile occupies its own aggregate pack: the 2 MiB target
does not fit two 16,384-record proxies, and a leaf already exceeds the target.
This measures the aggregate writer path, not a many-small-tiles throughput
claim. Multi-tile aggregate correctness is covered by the small parity tests.

## Full-build results and delivery decision

All **32/32 builds completed**: eight warmups and 24 measured runs, none
excluded. The independent verifier rehashed **1,536 files**, checked complete
file lists and clean implementation provenance. Each profile's raw packs,
Zstd sidecars and manifest are identical to its old-implementation control.
No extra files were found inside the bundles, and the source hash is unchanged.
The common bundle IDs are:

- Individual: `sha256:0aa93af4eb8e316598c2461ae1d5f0f5e8cd1eef66898ab283ef86f29234ff66`.
- V4 aggregate: `sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

End-to-end wall seconds; the last column is the largest child RSS observed
among the three measured runs, not a configured RSS limit:

| Profile | Arm | Round 1 | Round 2 | Round 3 | Median | Time reduction vs old | Peak RSS MiB |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Individual, no LOD | Old | 4.756 | 5.113 | 5.128 | 5.113 | — | 190.5 |
| Individual, no LOD | New 1 | 4.619 | 4.990 | 4.797 | 4.797 | 6.18% | 151.0 |
| Individual, no LOD | New 2 | 3.535 | 3.650 | 3.665 | 3.650 | **28.62%** | 306.5 |
| Individual, no LOD | New 4 | 3.638 | 3.754 | 3.529 | 3.638 | 28.86% | 397.1 |
| V4, aggregate path | Old | 43.348 | 43.951 | 44.012 | 43.951 | — | 459.7 |
| V4, aggregate path | New 1 | 43.249 | 43.491 | 43.871 | 43.491 | 1.05% | 454.5 |
| V4, aggregate path | New 2 | 42.899 | 42.896 | 42.740 | 42.896 | **2.40%** | 509.4 |
| V4, aggregate path | New 4 | 42.916 | 42.252 | 43.716 | 42.916 | 2.36% | 530.8 |

Two-worker reductions relative to the old arm are positive in each paired
round: 25.68/28.62/28.52% without LOD and 1.04/2.40/2.89% for V4. Relative
to the new synchronous arm's median, two workers reduce time by 23.92%
and 1.37%, respectively. Thus the earlier codec-only 2.7–2.9x throughput
result must not be described as a complete-tiler speedup. The small V4
improvement does not justify increasing its default concurrency.

Peak queue reservations across all runs were 65,011,840 bytes / two tasks
and 130,023,680 bytes / four tasks, below the 128 MiB cap. No oversized job
used inline fallback in this pilot. Filesystem output blocks were identical
within each profile (3,329,416 individual; 3,381,408 V4); durability was not
weakened to obtain the gain.

**Decision:** deliver the bounded implementation as opt-in, recommend starting
with `--pack-workers 2` for individual-pack workloads, and retain one worker
by default. Four workers have no meaningful advantage here and use more RAM.
Rollback remains `--pack-workers 1`. No browser test is requested for this
byte-identical producer change; existing PLY visual qualification is neither
repeated nor expanded. No renderer speedup or full Saint-Etienne timing is
claimed. V4's much longer time motivates a separate profile of proxy generation
before any new optimization; the precise cause is not established by this test.

## Reproduction and retained evidence

- Machine: local i9-13900H, 20 logical CPUs, Ubuntu WSL2; not BIGZEN.
- Python 3.12, NumPy 2.4.6, python-zstandard 0.25.0.
- Source: 310,380,399 bytes, SHA-256
  `c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
- Evidence root:
  `/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827`.
  Includes the reference worktree, source, all bundles, per-run reports and
  stdout/stderr, inventories, `protocol.json`, `trials.jsonl`, `summary.json`
  and `verified-results.json`. No failed or warmup run is discarded.
- Versioned [driver](gstile-parallel-builds.mjs),
  [post-cohort verifier](gstile-parallel-builds-audit.mjs) and
  [verified results](gstile-parallel-builds-results.json).

For a new cohort use a new evidence directory, generate the source with
`tools/benchmark_gstile_tiler.py generate --records 1048576`, retain a detached
baseline checkout as `baseline`, and run the driver with that directory as its
argument. The driver intentionally pins the source hash, runtime and machine
paths; declare and retain a new protocol before changing those. It refuses a
dirty candidate or an existing results directory. Run the verifier only after
the cohort completes, never concurrently with timing. The measurements use
warm filesystem caches and three measured repetitions, not confidence intervals
or a large-scene production qualification.
