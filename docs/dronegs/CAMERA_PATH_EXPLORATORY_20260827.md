# GSTile — door-to-facade exploratory comparison, 2026-08-27

**Collection complete; formal qualification not passed.** Two warmups and six
AB/BA pairs are retained, including the arm whose idle cadence control failed.
The operator authorized an exploratory continuation after that interruption.
There is no retrospective removal of the failure, selective rerun, or claim
of a qualified FPS, input-to-photon or product-wide speedup.

## Outcome

Aligned staging reduces the measured host cut-commit stage in every one of the
six pairs on both primary door-to-facade phases. The paired median reduction is
24.62% on first passage and 24.74% on revisit. End-to-end cut readiness remains
around ten seconds: the paired median change is +0.53% and -1.44%, respectively.
This descriptive interrupted experiment does not establish an overall speedup.

| Phase | Reference commit median | Candidate commit median | Paired commit change | Reference readiness median | Candidate readiness median | Paired readiness change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| First door → facade | 137.90 ms | 106.00 ms | -24.62% | 10031.40 ms | 10140.50 ms | +0.53% |
| Revisit door → facade | 137.00 ms | 105.05 ms | -24.74% | 10344.50 ms | 10142.10 ms | -1.44% |

Paired change is `median(candidate_i / reference_i) - 1`, **not** the ratio of
the separately reported medians. The primary readiness endpoint is elapsed
time from the last camera input being applied to the final cut-commit event.
The later settlement/idle controls are excluded. The commit stage includes
resource preparation/upload/copy/attachment; it is not a GPU copy timestamp.

The load-stage medians are 9.77/9.91 seconds (reference/candidate first passage)
and 10.07/9.91 seconds (revisit). Each primary phase completes 729–772 MB of
logical network payload, including 128–179 MB of prefetch. These scheduler
bytes are not NIC traffic measurements. The bounded RAM revisit still has
318–341 cache misses and only 6–8 hits across the primary measurements.
No persistent-cache hits or network retries are recorded. The experiment
therefore points to loading/transport/cache work as the next priority, not a
larger claim about the roughly 30 ms median commit-stage reduction.

## Exact source, dataset and environment

- Reference: `fdc2b042b8075809cc75dc54262fc9ed5c2d7ae1` (PR #274).
- Candidate: `044ec64829ce3ae4aeff5e1a51962413e97002a4` (PR #275 merge).
  Runtime changes end at `0627d65516cc585518af21784ee612606c48f6ba`; later
  commits are documentation. See [integrated staging evidence](ALIGNED_STAGING_INTEGRATION_20260827.md).
- Saint-Etienne bundle:
  `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
- Windows Chrome 151.0.0.0; recorded adapter vendor NVIDIA, architecture
  Lovelace; 20 reported hardware threads. The same qualification workstation
  reports RTX 4070 Laptop, driver 610.62, 8,188 MiB VRAM. Node 24.14.0 runs
  the harness server/analyzers in Ubuntu WSL. Patched PlayCanvas 2.21.4.
- Fixed 1200×675 drawing buffer, DPR 1, vertical FOV 42°, `merged` assembly,
  packed transforms, directional opacity, existing Workers and LOD policy.
  Source TS, instrumented TS, compiled JS, engine files and protocol hashes
  are retained and independently verified.

The authoritative checkout was clean at the candidate merge. This comparison
changes neither application code nor the production service on port 3000.
The isolated loopback server on 3019 proxies the existing BIGZEN qualification
API/tunnel on 30080. It is not a disk-local BIGZEN throughput benchmark.

## Frozen path and cache conditions

Each arm uses a fresh document/backend/scheduler. Load the normal overview,
prepare the door and settle, then execute first door→facade, return facade→door,
and revisit door→facade. Each motion applies 41 predetermined poses over
1,200 ms. The existing 120 ms LOD debounce and prefetch remain unchanged.
The return is retained as a secondary phase, not substituted for a primary.

Door local target `[0, 3.05, 1.6]`, distance 3.7; facade target
`[0, -1.8, 1.6]`, distance 10.5. `camera.mjs` derives world-space poses from the
same descriptor frame; every input and final view matrix is checked.

The scheduler has a 768 MiB RAM LRU. No IndexedDB cache is opened and HTTP
responses are `no-store`; no existing user cache is deleted. Overview/door
preparation may already warm ranges, so “first passage” is not an empty-cache
facade load. The revisit retains only that arm's bounded RAM cache and need
not be network-free. OS/disk caches, network contention and thermal state are
uncontrolled. The active descriptor exposes raw identity pack URLs, not Zstd
encoding URLs. This does **not** test compression or production persistent-cache
benefits. It also does not prove the corresponding production features absent.

## Complete individual primary measurements

All values are milliseconds, reference / candidate. Pair 2 remains included
and explicitly marked despite its failed control; no favorable filtering.

| Pair | Segment | First commit | First readiness | Revisit commit | Revisit readiness |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | Original | 144.6 / 105.6 | 10277.3 / 10313.4 | 157.5 / 105.9 | 10346.8 / 10431.9 |
| 2 | Original; reference post-control failed | 127.4 / 108.8 | 10155.7 / 10058.8 | 123.1 / 110.2 | 10384.8 / 10245.6 |
| 3 | Exploratory continuation | 140.0 / 100.7 | 9907.1 / 9415.6 | 149.3 / 104.2 | 10196.6 / 10038.6 |
| 4 | Exploratory continuation | 141.8 / 106.7 | 9645.5 / 9871.9 | 136.2 / 110.3 | 10342.2 / 9614.4 |
| 5 | Exploratory continuation | 135.8 / 106.4 | 9901.4 / 10222.2 | 129.2 / 98.0 | 10533.6 / 10457.9 |
| 6 | Exploratory continuation | 134.4 / 101.5 | 10447.9 / 10521.1 | 137.8 / 102.9 | 10112.9 / 9798.4 |

Secondary return: commit medians 137.80/102.40 ms, paired change -24.31%;
readiness medians 10246.30/10093.65 ms, paired change -0.69%. All individual
secondary values and all raw frames/long tasks remain in the retained JSON.

## Failures, continuation and acceptance limits

1. Earlier setup attempts omitted the patched engine or its sibling dependency.
   Their failures and original sources are retained and excluded.
2. The earlier camera pilot suffered sustained roughly 1 Hz callback delivery,
   even in separate no-render controls. Its entire timing series is quarantined;
   it is not combined with this foreground collection.
3. Foreground v2 rejected equal adjacent `performance.now()` timestamps.
   That was a classifier bug: v3 accepts nonnegative deltas and keeps all raw
   samples. The original failure and exact-data regression replay are retained.
4. Foreground v3 completed five arms, then arm 6 completed its camera paths
   but failed the idle post-control: 290.5 ms maximum versus the fixed 250 ms
   limit, 828 callbacks/6.02 s, median 7.1 ms. Focus/visibility stayed valid.
   No reported long task overlaps that gap; its cause remains unidentified.
   There is no recorded renderer/Worker/GPU error. The strict validator still
   rejects this original cohort; the failure is not erased or reclassified.
5. The operator authorized only the remaining eight arms in exploratory mode.
   Cadence failures would be warnings while retaining the exact same failed
   threshold/result; renderer, foreground and persistence errors still stop.
   Every continuation result permanently records `qualified:false`.

Original cohort `fg-20260827171526316-9b0bde78`: 17:15:26–17:25:12 UTC.
Continuation `fg-resume-20260827-9b0bde78`: 17:52:46–18:05:36 UTC.
The 1,653.881-second interruption and changed collection policy remain explicit
confounders. All 16 continuation controls pass; overall 27 of 28 controls pass.
No threshold was raised and no unfavorable arm was rerun.

All 14 arms pass the independent core checks: original/source/engine hashes,
input poses, view matrices, coherent timing generations, settled queues,
single merged entity/resource with eleven streams, active/resident equality,
no Worker failure/fallback or network retry. Each pair has exactly equal
initial, door and final selected IDs/counts/camera matrices. Initial count is
7,461,366, door 7,453,039 and facade 7,450,287. These are contract checks, not
a new pixelwise PLY image metric; prior human visual acceptance remains the
separate [PR #275 evidence](ALIGNED_STAGING_INTEGRATION_20260827.md).

The harness uses native ESM plus the real backend, not the production Next/React
shell. Initialization/navigation are outside the primary endpoint. GPU queue
completion is awaited at settlement, not on each frame. rAF callback counts
are not presented-frame FPS. Other paths, scenes, GPUs and browsers remain
unqualified. No statistical significance or universal speedup is asserted.

## Reproduction, retained artifacts and next work

Evidence is retained on WSL and mirrored to BIGZEN at:
`/home/olivier/droneai-qualifications/gstile-camera-benchmark-20260827`.
Nothing was removed: old pilots, failed setups, original modules, engine tree,
all raw JSON, protocol manifests, network logs and diagnostic scripts remain.

From that directory:

```bash
sha256sum -c foreground-v3/frozen.sha256
sha256sum -c exploratory/frozen.sha256
node exploratory/analyze.mjs <new-analysis-output.json>
```

`exploratory/full-analysis.json` verifies all 14 arms and six paired endpoints;
`exploratory/summary.json` contains individual/median/paired-ratio statistics.
`summarize-exploratory.mjs` and `review-summary.mjs` derive/cross-check those
numbers. `exploratory/manifest.json` binds the first six files by SHA-256.
`evidence.sha256` covers the archived directory. Analysis output is exclusive
(`wx`); use new filenames rather than overwrite existing evidence.

Preparation passed 27 continuation tests, 23 unchanged cadence tests and the
camera contracts. The real 290.5 ms failure is replayed in tests; it remains
failed while exploratory collection proceeds. HTTP boundary checks covered
missing results, and tests cover no replay/overwrite, wrong identity, saved
failure, server errors, ordering and unchanged measured function bodies.

Next priority is a separate lossless loading experiment: verify why this test
descriptor selects identity URLs, then compare available compressed transport
and production persistent-cache paths with exact decoded hashes and fixed
door→facade/revisit conditions. Quantify useful prefetch bytes and cache churn
before changing budgets or aggregation defaults. Do not lower visual quality,
reuse this interrupted series as a formal baseline, or infer compression gains
from the staging result. No further renderer/tiler default is changed here.
