# GSTile cache isolation — component qualification

Date: 2026-08-27. Status: code and real-browser component checks pass;
camera-path performance and new operator visual acceptance remain pending.

## Findings and implementation

| Finding | Evidence | Change | Acceptance |
| --- | --- | --- | --- |
| Disk reads held network slots | Both cross-resource blocking regression tests fail on the reference | Independent bounded disk/network queues; disk default two, viewer network limit still six | A disk hit completes while network is blocked, and vice versa |
| Speculative scans evicted demanded ranges | Three demand ranges followed by four predictions caused ten transfers, including three demand reloads | Byte-bounded segmented LRU, protected demand up to 75% of unchanged RAM cap | Same deterministic trace needs seven transfers, three cache hits, identical bytes |
| Prior raw-only camera bench did not qualify production cache/compression | Descriptor bootstrap did not negotiate Zstd; HTTP/IndexedDB disabled | Separate eight-pack Chrome component experiment using native HTTP Zstd and real IndexedDB | Exact hashes on raw, compressed, RAM and disk paths |

No tiler, renderer shader, decode, LOD selection, Gaussian representation,
quality threshold, or production cache schema is modified. A demoted demand
range remains in probation until actual eviction. Demand can use the entire
cache; the protected segment is a maximum, not a permanently reserved buffer.
Predictions too large for currently unprotected capacity are returned to their
caller but not retained in RAM. They still undergo existing verification and
may be persisted. The initial 75% split and two-reader limit are bounded
policies awaiting broader trace tuning, not universal performance optima.
The cache byte ceiling is unchanged, not a promise of identical peak process
memory: up to two disk reads may now overlap network/decode work. Peak memory
under the complete camera workload remains a rollout measurement.

Cancellation, shared consumers, critical promotion, immutable identity across
signed URL rotation, raw fallback and bounded network retry retain their
contracts. Persistent misses release their disk slot before joining the
network queue. Persistent failures remain advisory; integrity verification
remains mandatory before persistent writes and renderer use.

## Automated checks

- Three new regression tests failed on the reference for the expected reasons
  and pass on the candidate.
- 23 new tests cover cache accounting, large entries, speculative/demand
  recency, a deterministic 1,000-operation mixed trace, independent pools,
  priority promotion, shared requests, cancellation and storage failure/retry.
- All 359 frontend tests pass (41 files), including 308 GSTile tests.
- TypeScript, focused ESLint and the production Next.js build pass.
- One intermediate test fixture incorrectly reused a consumed `Response`;
  it was corrected to clone the response, without changing implementation.

Commands from `app4-dashboard/frontend`:

```bash
npm test
npm run typecheck
npx eslint app/lib/gstile/range-source.ts app/lib/gstile/memory-range-cache.ts app/lib/gstile/range-cache-isolation.test.ts app/lib/gstile/memory-range-cache.test.ts
npm run build
```

## Real Chrome component experiment

Reference: `c0aae7e494c301842bad3cf55bf358f0ee7f0ca0`. Candidate source and
generated ES modules are frozen by SHA-256 in `provenance.json`, before the
experiment; candidate was an uncommitted worktree based on that reference.
The analyzer independently rechecks every source/module/harness hash.

Environment: Chrome 151 on the Windows qualification workstation; HTTP proxy
and transpilation in local Ubuntu WSL, Node 24.14.0; actual remote bundle
served through the existing BIGZEN API tunnel. There is no GPU workload in
this component experiment. It is not a BIGZEN-local disk bandwidth test.

Bundle: `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Select eight packs at evenly spaced descriptor indices, endpoints included.
Pack IDs, lengths and expected hashes are retained in `descriptor.json`.
Test origin is `http://127.0.0.1:3020`; no user cache is cleared. The component
uses 128 MiB RAM and IndexedDB caps to cover these eight packs (production
defaults remain 768 MiB and 2 GiB). Unique per-run/arm identities ensure cold
persistent phases, and writes are explicitly drained before warm reads.

Order: reference, candidate, candidate, reference. Each arm tests raw and
Zstd cold network, immediate RAM reuse, then cold and warm persistent cache
with a new scheduler. Raw/Zstd order alternates. HTTP responses are no-store.
Each load is followed by SHA-256 comparison against all eight manifest
hashes. Timers cover load only, not the subsequent integrity check.

Result `component-1787856731033.json` passes all 24 phases and 192 individual
hash comparisons. There are zero retries, compression fallbacks or cache
errors; all queues drain. Independent server accounting confirms 96 complete
requests and 206,099,024 payload bytes across the experiment.

| Path | Bytes for eight packs | Observation |
| --- | ---: | --- |
| Raw network | 20,550,016 | Exact manifest hashes |
| Native HTTP Zstd network | 15,487,370 | 24.64% fewer payload bytes, same decoded hashes |
| Warm RAM | 0 additional network bytes | Eight hits in every warm-RAM phase |
| Warm IndexedDB, fresh scheduler | 0 network bytes | Eight disk hits in each of four arms |

Compression/cache savings validate **existing** features, not a gain newly
introduced by this change. Warm disk load durations are reference 30.4/55.4 ms
and candidate 38.8/38.4 ms. This tiny sample does not establish a disk-read
speedup or regression. First raw loading includes connection/server warmup;
OS caches, scheduling and thermal state are uncontrolled. No latency ratio,
FPS, input-to-photon or scene-wide speedup is claimed.

The eight-pack test fits entirely in memory and does not qualify cache churn
on the real door-to-facade path. The seven-versus-ten transfer result is a
deterministic regression fixture, not a Saint-Etienne measured improvement.
Real camera-path traffic, cache misses, protected-segment usefulness, rejected
predictions, long tasks and final-cut parity remain the next measurements.

## Full viewer smoke check

The production Next.js build was served locally behind the read-only preview
proxy on 3022. In Chrome it reached `Prêt`, `budget-limited`, zero pending
nodes, 374 selected/target nodes and 7,461,366 resident/target Gaussians, with
zero decode-Worker fallbacks. The displayed model was inspected for successful
loading. This is a startup/integration check, not a new operator PLY parity
acceptance or a repeatable camera benchmark. No timing gain is inferred from
that single startup.

## Retained evidence and rollout

All sources, protocol, network logs, results and independent analysis are
retained at `/home/olivier/droneai-qualifications/gstile-cache-isolation-20260827`.
No previous benchmark, cache, bundle or output was deleted. The isolated
component server uses 3020. A production-build candidate preview can run on
3022, using a read-only same-origin proxy to the existing qualification API;
the service on 3000 and production CORS policy are not changed.

Rollback is a code revert; no data migration or cache deletion is required.
Batching persistent writes/recency, compressed-at-rest storage and new pack
formats are deliberately deferred to separate phases.
