# GSTile cache isolation — component and camera qualification

Date: 2026-08-27. Status: code, real-browser component checks, operator
visual acceptance and the complete compressed/persistent camera pilot pass.
Total process peak-memory qualification remains pending; PR #277 stays draft.

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
predictions, long tasks and final-cut parity were subsequently measured in the
separate complete pilot below.

## Full viewer smoke check

The production Next.js build was served locally behind the read-only preview
proxy on 3022. In Chrome it reached `Prêt`, `budget-limited`, zero pending
nodes, 374 selected/target nodes and 7,461,366 resident/target Gaussians, with
zero decode-Worker fallbacks. The displayed model was inspected for successful
loading. This is a startup/integration check, not a new operator PLY parity
acceptance or a repeatable camera benchmark. No timing gain is inferred from
that single startup.

## Retained evidence and rollout

The operator confirmed visual conformity of candidate commit
`9ee1bb579fa620d7adf06fdfa51e3ac7640e346d` on the 3022 preview with “validé”.
This acceptance does not convert the component timings into a camera-path
speedup and does not waive the pending memory/performance gates.

## Subsequent camera pilot: stopped on the reference

A separate frozen pilot enabled Zstd and real IndexedDB on the unchanged
door-to-facade path, with a fresh retained database per arm. It planned two
warmups followed by two AB/BA pairs. It stopped after the first **reference**
warmup because the post-run idle cadence control failed. No candidate arm
or measured pair was collected; this is not evidence of a candidate regression.

The pre-control delivered 807 callbacks in 6.0235 s, median gap 7 ms and
maximum 39 ms. The post-control delivered only six in 6.0226 s, median gap
1,004 ms and maximum 1,004.6 ms. The unchanged limits require at least 180
callbacks, median below 40 ms and maximum at most 250 ms. Focus/visibility
remained true/visible and there were no reported runtime, GPU, Worker,
compression or persistent-cache errors. No long task overlaps the failed
post-control. During the camera phases, input timers remained around 30 ms
and memory sampling around 100 ms, while render callbacks were about 1,004
ms apart. This suggests browser/compositor cadence throttling rather than a
continuous JS stall; the underlying cause is not established.

All three reference phases finished with zero pending network work and the
expected 7,450,287 / 7,453,039 / 7,450,287 resident Gaussians. The revisit
recorded 283 persistent hits and 85,957,718 network payload bytes, all of the
latter speculative prefetch. Those counts are diagnostic observations, not
a qualified latency gain. The failed reference's timings are quarantined.
Its sampled RAM-cache peak was 805,269,376 bytes (below the 805,306,368 cap),
and the engine's logical GPU accounting peaked at 2,112,079,132 bytes. Windows
Chrome aggregate private bytes peaked at 13,296,832,512 across all Chrome
processes: this is not ownership-correct per-tab memory or physical VRAM.

The failed cohort, raw frames/controls, OS memory samples, frozen sources and
independent diagnostics are retained at
`/home/olivier/droneai-qualifications/gstile-cache-path-20260827`.
No failing passage was retried, removed or relaxed. The separate complete
cohort on 3025 subsequently finished after manual foreground launch, with the
same thresholds and source commits, an isolated origin and no pre-existing
cache. The failed cohort remains quarantined, not pooled into its results.

## Complete manually launched camera pilot

The operator's screenshot confirms six saved passages on 3025. The raw
records cover 19:17:06–19:23:17 UTC on 2026-08-27. Reference is
`c0aae7e494c301842bad3cf55bf358f0ee7f0ca0`; candidate is
`9ee1bb579fa620d7adf06fdfa51e3ac7640e346d`. Subsequent commits change docs only.
The analyzer verifies frozen source, instrumentation, generated module and
engine hashes. Chrome 151 on Windows reports an NVIDIA Lovelace adapter;
the result does not identify a precise GPU SKU or driver. The server runs
in local Ubuntu WSL, Node 24.14.0, via the existing BIGZEN API tunnel.

Configuration is fixed: merged backend, packed transforms, directional
opacity, Worker assembly and decode-input recycling; 1200 × 675 pixels at
DPR 1, vertical FOV 42°, 41 camera poses over 1,200 ms. The RAM cap is
768 MiB, persistent cap 2 GiB, with a fresh retained IndexedDB per arm.
Native HTTP Zstd is negotiated; HTTP responses are no-store. Preparation
loads the overview and then the door before the three camera movements.
Therefore **first door-to-facade is not a cold page-load metric**.

Order is two warmups followed by reference/candidate and candidate/reference.
Both warmups are retained but excluded from paired summaries. All six
passages complete. Recalculation from the raw samples confirms all 12 idle
controls pass their unchanged limits: 823–872 callbacks per approximately
six seconds, median gap 7.0–7.1 ms, maximum gap 147.5 ms. Foreground remains
valid. Idle rAF cadence is not a measurement of presented render FPS.

Independent proxy logs match scheduler totals exactly for every arm:
5,556 complete Zstd requests and 10,593,183,632 payload bytes across all six
passages, including preparation and warmups. No partial request, retry,
compression fallback, persistent error, runtime/GPU error or Worker fallback
is recorded. Phase deltas are also recomputed from cumulative counters.

Every paired endpoint has identical selected node IDs, camera and resident
counts. The five successive counts are 7,461,366 / 7,453,039 / 7,450,287 /
7,453,039 / 7,450,287 Gaussians, in a single enabled merged resource with
11 streams. This structural parity complements the operator's earlier
visual acceptance; it is not a new pixel-error or PSNR measurement.

### Measured pairs and interpretation

Readiness is the last render-data commit after the last camera input; it
excludes the trailing quiet period and persistent-write drain. It is not
input-to-photon latency. Network amounts are payload bytes, not HTTP/TLS
overhead. MB in the table is decimal. Lower is better.

| Primary phase | Pair | Reference ms | Candidate ms | Change | Reference MB | Candidate MB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| First door → facade | 1 | 2,965.1 | 3,260.4 | +9.96% | 239.751 | 192.278 |
| First door → facade | 2 | 3,187.4 | 2,781.1 | −12.75% | 239.537 | 192.278 |
| Revisit door → facade | 1 | 2,096.5 | 1,806.3 | −13.84% | 85.867 | 75.510 |
| Revisit door → facade | 2 | 2,206.8 | 1,774.8 | −19.58% | 85.941 | 75.638 |

The frozen pilot policy allows candidate readiness up to (reference × 1.10)
plus 50 ms, network bytes up to reference × 1.05, and sampled engine logical
GPU peak up to reference × 1.05. All four primary comparisons pass all three
checks. The first pair's +295.3 ms regression is retained and within policy;
it is not hidden by the aggregate.

| Phase | Median reference → candidate | Ratio-of-medians change | Median network change |
| --- | --- | ---: | ---: |
| First door → facade | 3,076.25 → 3,020.75 ms | −1.80% | −19.77% |
| Revisit door → facade | 2,151.65 → 1,790.55 ms | −16.78% | −12.03% |
| Return facade → door, secondary observation | 2,103.05 → 1,630.85 ms | −22.45% | −41.84% |

These are descriptive results from **two pairs on one path and machine**,
not statistical proof or a universal speedup. The secondary return movement
was recorded but is not promoted to a new acceptance endpoint after seeing
the results. First-trip latency is mixed; the defensible first-trip finding
is lower transferred volume. Revisit latency improves in both pairs.

First-trip RAM hits increase from 2 to 36, and revisit hits from 1 to 33 in
each measured pair. Revisit persistent hits fall from 283 to 251 as more
ranges are served from RAM. All revisit network bytes in both arms remain
speculative prefetch: approximately 75.6 MB on the candidate versus 85.9 MB
on the reference. There are no rejected prediction admissions. The combined
patch qualifies demand protection plus queue isolation; this experiment
does not isolate the latency contribution of either component.

There is one reported long task per primary phase in each measured arm:
first-trip reference 135/110 ms versus candidate 125/133 ms; revisit reference
115/117 ms versus candidate 129/148 ms. No reduction in main-thread long
tasks is established. The remaining prefetch traffic and persistent write
path are follow-up hypotheses, not evidence that the network alone remains
the readiness bottleneck.

### Memory evidence and unchanged rollout gate

| Measured arm | Sampled cache peak, bytes | Engine logical GPU peak, bytes | Pending write peak |
| --- | ---: | ---: | ---: |
| Pair 1 reference | 805,290,560 | 2,111,976,732 | 8 |
| Pair 1 candidate | 805,302,368 | 2,111,976,732 | 5 |
| Pair 2 candidate | 805,305,792 | 2,111,976,732 | 7 |
| Pair 2 reference | 805,303,776 | 2,111,874,332 | 5 |

Memory is sampled approximately every 100 ms, with 455–510 samples per
measured arm. Every sampled cache value stays within 805,306,368 bytes;
protected bytes stay within 603,979,776. Candidate active pools peak at six
network operations and two persistent reads. The reference does not expose
separate pool counters; it must not be interpreted as having zero activity.
All queues and tracked persistent writes drain at completion.

The logical GPU peak is the sum of engine tex/vb/ib/ub/sb accounting, not
physical VRAM. The largest bundle pack is 6,291,488 bytes: two live disk-read
payloads can represent 12,582,976 bytes. That payload-only calculation is
**not** a total allocation bound; Blob copies, persistence queues, worker
buffers, deferred GC and browser allocations are outside it. The candidate
warmup also reaches 12 pending writes versus six on the reference warmup;
the measured-pair values alone do not establish a universal queue bound.

No OS process-memory trace was recorded for this manually launched cohort.
The earlier failed cohort's Chrome aggregate cannot be substituted, and a
post-run snapshot cannot recover a missing peak. Consequently
`fullProcessPeakQualified` remains false. The performance pilot is accepted,
but PR #277 remains draft with **no merge or production rollout** until a
separate predeclared memory qualification is complete. It must attribute
memory to the tested process tree (or use a dedicated browser process tree),
retain baseline/candidate raw samples and cover transient overlapping work.
No additional visual acceptance is needed for this unchanged code.

### Successful-cohort evidence

Retained directory:
`/home/olivier/droneai-qualifications/gstile-cache-path-manual-20260827`.
It contains six exclusive-write result files, frozen protocol/provenance,
sources and engine, raw network logs, the operator's completion screenshot,
`analysis-completed.json`, `audit-completed.json`, `conclusion.json`, their
scripts and an SHA-256 evidence manifest. The post-run audit retains raw
reference pool placeholders as zero; the final conclusion correctly marks
these unavailable counters as null. No interpretation uses those placeholders.

Recompute from the retained directory with Node (new output filenames avoid
overwriting evidence): `node analyze.mjs analysis-recheck.json`. The camera
contracts and 23 foreground-control cases also pass via
`node --test camera.test.mjs controls.test.mjs`. Production source was not
changed during analysis; the existing 359-test/build evidence remains tied
to candidate `9ee1bb5`. Documentation changes receive their own checks.

## Operations and follow-up

All sources, protocol, network logs, results and independent analysis are
retained at `/home/olivier/droneai-qualifications/gstile-cache-isolation-20260827`.
No previous benchmark, cache, bundle or output was deleted. The isolated
component server uses 3020. A production-build candidate preview can run on
3022, using a read-only same-origin proxy to the existing qualification API;
the service on 3000 and production CORS policy are not changed.

Rollback is a code revert; no data migration or cache deletion is required.
Batching persistent writes/recency, compressed-at-rest storage and new pack
formats are deliberately deferred to separate phases.
