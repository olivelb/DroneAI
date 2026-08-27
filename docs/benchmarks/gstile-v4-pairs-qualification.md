# GSTile V4 direct pair matching

2026-08-28. Qualified exact optimization for V4; no new option required.

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

## Results and delivery decision

**10/10 builds completed**, including both warmups; no failed or excluded run.
The driver checked all files (630 total) and every report's clean pinned
implementation. A separate recursive binary `diff` also compared each of the
other nine output directories against the old warmup: no differences. Packs,
Zstd sidecars, manifest and source checksum are unchanged. All builds report
bundle ID `sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

Complete-build wall seconds; warmups are retained but not included in medians:

| Trial | Order | Old union-find | Direct pairs | Time reduction |
| --- | --- | ---: | ---: | ---: |
| Warmup | AB | 42.264 | 29.601 | — |
| Pair 1 | AB | 42.676 | 30.182 | 29.28% |
| Pair 2 | BA | 43.359 | 29.781 | 31.32% |
| Pair 3 | AB | 43.638 | 30.314 | 30.53% |
| Pair 4 | BA | 44.108 | 31.598 | 28.36% |
| Measured median | — | **43.498** | **30.248** | **30.46%** |

The median is the mean of the two middle observations. Both predeclared timing
criteria pass (at least 10% median reduction and every pair faster), as does
complete byte parity. Peak child RSS over measured runs is comparable:
469,016 KiB old versus 467,464 KiB new. No meaningful RSS reduction is claimed.
Filesystem output blocks are identical at 3,381,408 in every build. The gain
does not come from weaker durability, fewer proxies, lower quality or a changed
pack-worker count. The measured runtime is unchanged by the final docs commit.

**Decision:** deliver the direct pair matcher as the normal V4 implementation,
with no feature flag or format migration. Retain the old matcher only as a
test oracle, not a second production path. Existing bundles remain valid.
Non-V4 strategies and renderer code are untouched. Reverting this isolated
change restores the old implementation if a new dataset exposes a regression.
No additional visual run is requested because all canonical output bytes are
identical. This remains a local synthetic pilot, not a measured speedup on
BIGZEN or the full Saint-Etienne source. Do not add its percentage to the
separate parallel-pack preparation result.

## Provenance and reproduction

- Runtime commit: `33ab1c366d74bf8648153873b24691e45497a4bb`.
- Candidate including frozen driver: `eee54178795d2e70ef51320d679a075cb731de7e`.
- Python 3.12 / NumPy 2.4.6 / python-zstandard 0.25.0; local WSL2, not BIGZEN.
- Source: 310,380,399 bytes, generated by `tools/benchmark_gstile_tiler.py`
  with 1,048,576 records; checksum above. Correlated synthetic coefficients,
  not a substitute for a full real-scene capacity qualification.
- Retained evidence:
  `/home/olivier/droneai-qualifications/gstile-v4-pairs-20260828`, including
  the baseline worktree, ten output bundles, raw reports/stdout/stderr,
  complete inventories, `protocol.json`, `trials.jsonl`, `verified-results.json`
  and a copy of the exact [driver](gstile-v4-pairs-builds.mjs).
- Original diagnostic (excluded from timing):
  `/home/olivier/droneai-qualifications/gstile-v4-profile-20260828/v4.prof`.

Reproduce with a new evidence directory and a retained baseline checkout named
`baseline`. Run `node docs/benchmarks/gstile-v4-pairs-builds.mjs <evidence>` from
the clean candidate. The driver pins implementation, fixture hash and paths;
declare a new protocol before altering them. It refuses an existing results
directory, retains failures and checks the source hash again after all trials.
No existing artifacts are removed. Raw report hashes and complete baseline
inventory are included in the versioned [results](gstile-v4-pairs-results.json).
