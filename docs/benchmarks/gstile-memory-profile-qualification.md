# GSTile explicit desktop RAM-cache pilot

Date: 2026-08-27. Status: stabilized six-run cohort complete; operator confirms
visual conformity to the PLY. Accepted for opt-in delivery under the user's
revised criteria, with the network regression explicitly retained below.
Base: `43952f7f745f308e64ce76d9eb8f76df943f46ce` (draft PR #279).
Runtime implementation pinned in both arms:
`fe7c069fc3821693e92bf27d42531771538439ac`.

## Decision and scope

Add `gstileMemoryCache=desktop` as an explicit opt-in to a **1,536 MiB** raw
pack cache. Missing, `standard` and unrecognized values retain **768 MiB**.
`gstileMemoryCacheBytes()` is used by both the React viewer and the manual
qualification harness. The viewer exposes `data-memory-cache-bytes`.
No browser memory heuristic, unbounded numeric query, eager allocation,
persistent setting or new browser API is introduced.

The byte-bounded segmented LRU, protected fraction (75%), six network slots,
two IndexedDB slots, 2 GiB persistent cache, decoding, GPU arena, selected
cut and scientific coefficients are unchanged. The earlier freshness fix
and 64 MiB speculative floor are present in **both** arms; this experiment
cannot be used to attribute their individual effects.

The accepted door/facade cuts need 1,323,586,240 bytes (1,262.27 MiB) together.
768 MiB cannot retain them both; 1,536 MiB leaves 273.73 MiB of raw capacity.
The old revisit recorded 33 RAM hits and 251 IndexedDB hits. See the
[capacity analysis and limits](gstile-prefetch-budget-qualification.md).
This motivates an experiment, not a promised gain: prefetch and SLRU eviction
may still displace data. Q96 cache hits still need decode/copies/GPU work.

## Predeclared manual protocol

Sources are versioned in [gstile-memory-profile-manual](gstile-memory-profile-manual/).
The serving/evidence directory is
`/home/olivier/droneai-qualifications/gstile-memory-stabilized-20260827`.
The dedicated server is `http://127.0.0.1:3029/`; existing services and old
cohorts are not replaced. The user starts the benchmark in a new tab of the
already-open Chrome. **Opening the page does not run measurements.**

- Two warmups, then two paired comparisons in AB/BA order, all six retained.
- A = standard/768 MiB, B = desktop/1,536 MiB, identical source commit and
  identical instrumented modules (verified before freezing).
- Each arm: initialization, settled door, door→facade, facade→door,
  door→facade. Fixed 41-input, 1,200 ms gestures; 1,200×675, DPR 1, FOV 42.
- Separate fresh IndexedDB name for every passage; no clearing other caches.
  Pending disk writes and speculative requests drain at each settled endpoint.
- No HTTP cache; native Zstd required, no fallback/retry/errors accepted.
- Pre/post 6-second cadence controls retain the earlier thresholds:
  minimum 180 frames, median gap <40 ms, maximum gap ≤250 ms.
- Protocol v2 adds a fixed 5,000 ms stabilization before **each** pre/post
  control in both arms. Its samples and elapsed duration are recorded
  separately. The deadline never depends on observed cadence and a failed
  control is never retried. Foreground/runtime requirements remain active
  during stabilization. The analyzer verifies both stabilization windows.
- Loss of foreground, control failure or runtime error stops the cohort;
  failed attempts remain saved. No selective retry or threshold relaxation.
- Primary outcome: revisit `afterInputMs`; secondary: return and first
  transition, RAM/disk hits, network payload, long tasks, logical GPU peak.
- Predeclared non-regression tolerances: readiness +10% +50 ms, network +5%,
  logical GPU +5%, with each raw-cache cap checked separately. These are
  acceptance tolerances, not a minimum gain or a statistical confidence bound.
- Exact settled node sets, Gaussian counts, camera matrices, merged resource
  count, streams and backend errors are checked by the analyzer. They do not
  replace the operator's visual check against the PLY.

The page automatically advances only after the first manual click. Keep that
Chrome window foreground for roughly six minutes; do not navigate away.
Results use exclusive-create writes. A server restart may renew signed URLs
only for the same bundle/manifest; it cannot replace the frozen descriptor.

## Memory interpretation

The candidate permits up to **768 MiB extra retained raw data**, not necessarily
768 MiB extra total process peak. Decode buffers, IndexedDB serialization,
assembly staging, engine resources, delayed reclamation and other tabs remain.
The existing Chrome session is suitable for this operator timing pilot, but
not clean per-tab process-memory attribution. Samples of logical allocations
are not RSS, physical GPU memory or proof of absence of short transient peaks.
`fullProcessPeakQualified` remains false: total process memory was not measured.
The user explicitly removed that measurement as a merge prerequisite on
2026-08-27. This retires the delivery gate; it does not turn it into a pass.
The desktop profile remains opt-in, not a universal default.

## Completed stabilized cohort and delivery decision

All six passages and all twelve pre/post cadence controls completed. No
foreground, runtime, GPU, fallback, retry or persistent-cache error was recorded.
Settled node IDs, Gaussian counts, cameras, eleven streams and the single merged
resource match between arms. The operator subsequently confirmed visual
conformity to the original PLY. Both arms use the pinned runtime above and bundle
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.

Medians over the two measured AB/BA pairs, excluding both warmups:

| Gesture | 768 MiB after-input ms | 1,536 MiB after-input ms | Reduction |
| --- | ---: | ---: | ---: |
| First door → facade | 2,823.75 | 2,333.60 | 17.36% |
| Return facade → door | 1,468.70 | 1,274.25 | 13.24% |
| Revisit door → facade | 1,718.70 | 1,349.75 | 21.47% |

All six individual paired gesture comparisons improve. This is a small local
pilot, not a confidence interval or a universal speedup. Disk reads at return
and revisit fall from 188–237 per phase to zero. The sampled raw-cache peaks
respect both caps; the logical GPU peak remains about 2.112 GB. One long task
of roughly 98–129 ms per gesture remains: lag has not been eliminated.

**The frozen network tolerance failed.** Summed median compressed bytes over
the three gestures increase from 297,867,411 to 428,965,262 (+44.01%); whole-pass
bytes including preparation increase by 15.69%. Most are speculative; candidate
return also incurs two demand requests / 1,489,450 bytes. All six server logs
reconcile with the reported response counts and network totals.

Historical useful-byte credit is larger in the larger RAM cache. Its speculative
budgets remain 156 / 126 / 109 MiB, versus roughly 88 / 75 / 67 MiB in the
reference, even though motion prediction has expired and no newly useful
prefetch bytes are credited during these phases. This identifies stationary
halo budgeting as the next correction; it does not prove the precise disk
eviction history or predict the corrected latency.

The immutable `analysis-completed.json` still reports `complete: true`,
`pilotWithinTolerances: false`, `fullProcessPeakQualified: false`, and no execution
failures. Its SHA-256 is
`dc3414346fb9236c789e68f7ff21ac444ed883c227af987860157d214de893b5`.
The failed original cohort is also retained. No threshold or raw verdict has
been rewritten. After reviewing the measured gain and confirming visual
conformity, the user authorized PR/merge without total browser-memory testing.
Delivery therefore accepts the documented network tradeoff under this revised
policy; ordinary required CI must still pass. Default memory remains 768 MiB.

## Verification and reproduction

Ten new tests failed before the profile function existed, then passed.
Full frontend: **390 tests / 42 files** pass; TypeScript and scoped ESLint pass.
Scaled tests exercise the actual segmented-LRU implementation without GiB
allocations. Camera, cadence (23 assertions) and two profile-contract tests
pass. Syntax checks pass for harness, server and analyzer. Freeze validates
60 runtime modules (30 per arm) and 1,203 engine JS files.
The empty-cohort analysis correctly reports incomplete/unqualified, not success.
No separate GPU-kernel timing is claimed. For v2, six new
tests fail before implementation and pass afterward. All ten Node test-runner
cases pass, including the existing 23 cadence assertions; harness/analyzer
syntax checks pass. Tests cover fixed-window sampling, callback cleanup on
errors, invalid windows, foreground checks and rejection of a 253.3 ms gap
in the unchanged control after stabilization. The product sources have not
changed, so the already-passing frontend checks are not rerun locally.

For a new run, use a new evidence directory, port and database prefix; never
reuse a directory with results. Copy the versioned scripts there, keep the
source commit pinned, then:

```sh
node --test camera.test.mjs controls.test.mjs profile.test.mjs idle-window.test.mjs
node prepare.mjs
node server.mjs
# In a second terminal, after the descriptor has been captured:
node freeze.mjs
# Only now open the URL in Chrome; the operator clicks Start.
# After all six passages finish:
node analyze.mjs analysis-completed.json
```

The local frontend dependency tree must contain the repository's patched
PlayCanvas build. Do not rebuild a served `.next` directory during a pilot.
Raw results, descriptor URLs and compiled engine copies stay outside Git;
sources and protocol are committed. Rollback: remove the query parameter for
standard memory, or revert the profile commit. No dataset/cache migration.

## Original cohort interrupted — retained negative evidence

The original v1 cohort remains untouched at
`/home/olivier/droneai-qualifications/gstile-memory-profile-20260827`, port 3028.
Its frozen harness remains the correct analyzer for those results; do not
analyze or pool them with v2. Its status is **incomplete/unqualified**.

The reference warmup completed. The candidate stopped before fetching the
descriptor, creating its scheduler or initializing the renderer: zero memory
samples, no bundle ID, no camera phases. The pre-control recorded 801 frames,
median 7.1 ms and maximum gap **253.3 ms**, above the unchanged 250 ms limit.
The page remained visible/focused and recorded no runtime error or long task.
The worst gap ended 919.3 ms after the control began (control start 329 ms
after navigation). Navigation/browser cleanup is a hypothesis, not an
established cause. This is not evidence of a 1.5 GiB cache failure.

Both original files and `analysis-failed-20260827.json` are retained:

- reference SHA-256: `9d7c85f61ff8a7d0fd8c5c32ad58d21da40bb78e0faec344fe4906452e737ef4`;
- candidate SHA-256: `6805aa7dc701fc2362ae3df85257becf5e30de1d664641c1bffe328caaff4b2e`;
- analysis SHA-256: `c70decec4c2c103a0d504292571d7d857b706f42fd3f3c6ddc3208d981105447`.

The user approved a new, complete six-run cohort with a fixed stabilization.
V2 uses a new origin, evidence directory and IndexedDB prefix, but the same
runtime commit, source bundle, camera path, cache caps and cadence thresholds.
It is a declared protocol revision, not selective reuse of the successful
warmup or removal of bad samples. The new complete cohort passed its unchanged
cadence controls; this does not establish the cause of the earlier failure.

## Related audit

The [audit verification and phased proposal](../dronegs/OPTIMIZATION_AUDIT_REVIEW_20260827.md)
separates implemented features, historical measurements and untested proposals.
Other architectural changes are deliberately excluded from this comparison.
