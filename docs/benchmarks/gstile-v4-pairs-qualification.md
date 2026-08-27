# GSTile V4 direct pair matching

2026-08-28. Candidate implementation; full-build timings pending.

## Scope and exactness argument

V4's greedy matcher only joins two singleton groups. Once matched, an endpoint
cannot be accepted again during that generation. Therefore a byte-per-vertex
matched mask and flat roots replace union-find without changing selected edges.
Candidate order and costs, minimum-index greedy root, Morton-ordered completion
root, source-ID ordering, moment matching and opacity refitting are unchanged.
The completion pass selects the same consecutive unmatched pairs, now with
array indexing. Memoryview iteration avoids NumPy scalar boxing without copying
candidate arrays. No new dependency, parallelism, configuration or format.

The existing `count <= target` path remains unchanged. Every valid reduction
target is at least `ceil(count / 2)`; impossible targets retain a stall error.
The old implementation's size array and final root-search array disappear.
The mask consumes one byte per vertex; this is not a total process RSS bound.

## Verification before timing

Two new tests fail before implementation (missing specialized matcher; stopped
at first two failures). All **18 new tests pass**, and **88 affected tiler/probe
tests pass**, with scoped Ruff and diff checks.

- Test-only union-find oracle frozen from baseline `9269f8e`.
- All 720 edge orders on a complete four-vertex graph, two targets each.
- Eight seeded families with odd/even counts, partial targets, shuffled large
  uint64 source IDs, repeated edges, self edges, coincident centres, and fallback.
- Exact flat roots, group sizes/populations and input immutability.
- Completion preserves Morton orientation, not minimum-index orientation.
- Full multi-generation proxy record/error bytes, including coincident centres.
- Complete bundle byte parity with the old matcher: workers 1/2 and individual /
  multi-tile aggregate packs; odd 8,193-record source.
- Existing cancellation, cleanup, worker failure and atomic-publication tests.

## Predeclared full-build pilot

Reference: `9269f8ef22bbd21d1093fdb2f9be139180cad77a` in a retained detached
worktree. Candidate: clean implementation commit recorded by the driver.
One fixed synthetic 1,048,576-record SH3/directional source:
`c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Same local i9-13900H / Ubuntu WSL2, interpreter, filesystem and input.

Both arms: V4 adaptive-moment, 16,384 proxies, 65,536 leaves, 131,072 read chunks,
2 MiB aggregate target, **one pack worker**. No other tuning or environment
change. One warmup per arm, then four paired rounds ordered AB / BA / AB / BA.
All ten complete builds, raw reports and outputs retained; no exclusions or
OS cache flush. Fixture generation and output hashing excluded from wall time;
partition, proxy generation, encoding, Zstd, durable writes and publication
included. The prior cProfile run is diagnostic only, not a timing baseline.

Acceptance: all complete bundle files match the old arm byte-for-byte, source
unchanged, all reports clean and pinned, median wall time at least 10% lower and
each of the four paired candidate builds faster. This is a practical delivery
threshold, not statistical significance or a large-scene production claim.
Failure stops the cohort and is retained; do not selectively retry or adjust
the protocol after seeing timings. Default execution is optimized only after
this gate. Rollback is reverting this isolated matcher change.

No renderer/GPU changes and no repeat visual test if complete bytes are
identical. This does not expand the existing PLY visual qualification or claim
a speedup for the actual 50 M Saint-Etienne dataset. At these tile sizes the
aggregate writer produces one tile per pack; multi-tile correctness is covered
by the small tests, not an aggregate-throughput claim from this pilot.
