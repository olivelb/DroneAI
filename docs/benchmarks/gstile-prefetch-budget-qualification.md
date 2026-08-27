# GSTile low-utility prefetch floor — sequential replay

Date: 2026-08-27. Base: `a7a76f41a5c6a50528fde4d1f4558f4cb9c869dd`
(draft PR #278, stacked on cache-isolation PR #277).

## Decision and implementation

Lower the adaptive **speculative** budget floor from 96 to **64 MiB**. Keep
the cold/sample-gathering ceiling at 384 MiB, adaptation threshold at 128 MiB
completed prefetch, target useful-byte ratio at 50%, cumulative feedback and
whole-MiB quantization. A useful session can still recover the full 384 MiB.
Nothing in visible-cut selection, demand priority, resident Gaussian budget,
GPU assembly, decoding, compression or cache capacities changes.

The previous floor forced a 96 MiB plan even at only 8–12% reported utility.
The [freshness correction](gstile-prefetch-freshness-qualification.md) removed
stale predictions but did not reduce traffic: other halo packs filled that
floor. This phase changes the floor itself. No new flag, controller, persisted
state or browser API is introduced.

Floors of 96, 64 and 32 MiB were compared before changing the production
function. Both lower floors produced **identical ordered plans** in this
replay. Choose 64 MiB as the less aggressive change: 32 MiB adds no benefit
supported by these data. This is not a globally optimal setting or a proposed
universal browser memory limit.

## Method and scope

Input: the accepted `gstile-cache-path-manual-20260827` cohort. All three
candidate traces are included (warmup and two measured arms), using the
bundle `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
The [replay script](gstile-prefetch-budget-replay.mjs) pins inputs to the
SHA-256 fingerprints in the preceding freshness audit, imports the actual
selector/planner and checks the real budget function for the implemented floor.

Unlike the earlier one-step counterfactual, each policy starts once from the
recorded warmed door state and propagates newly downloaded pack identities
and cumulative completed-prefetch bytes through all three phases. Budgets
are recomputed from that changed state, not copied from later old snapshots.
The freshness fix is enabled in **both** modeled policies; all recorded motion
estimates were already expired at commit. This isolates the floor change from
the preceding freshness change.

The replay verifies exact visible node-id sets/counts, unchanged demand miss
identities, successful transport, absence of cancellations/retries and no
new-prefetch/next-demand intersection. Availability stays below the 2 GiB
persistent ceiling before each subsequent demand phase. It stops instead of
guessing if an earlier eviction or useful-hit ambiguity would require a full
RAM/IndexedDB event trace. Historical utility starts from the recorded state;
new prefetch has no later demanded hit on this particular path, so the useful
numerator stays fixed while the completed-byte denominator changes.

All three inputs have the same warmed state and settled geometry, so they
produce identical deterministic results. They are **not three independent
performance measurements**. Initialization/preparation, different future
trajectories, timing-dependent aborts and cache admission effects are not
qualified by this replay.

## Results: conditional traffic estimate, not measured latency

MB means decimal compressed payload bytes; MiB means decoded-byte budget.

| Phase | Budget 96-floor → 64-floor | Prefetch payload before → after | Prefetch requests before → after |
| --- | ---: | ---: | ---: |
| First door → facade | 96 → 90 MiB | 76.014 → 71.242 MB | 41 → 40 |
| Return facade → door | 96 → 76 MiB | 77.135 → 61.110 MB | 24 → 20 |
| Revisit door → facade | 96 → 68 MiB | 76.008 → 53.893 MB | 35 → 23 |

- Speculative payload across these phases: **229,156,774 → 186,244,742 bytes**,
  **−18.73%**, with 100 → 83 requests.
- Demand payload: unchanged, **116,332,206 bytes / 66 requests** on the first
  phase, then zero network demand on the return and revisit.
- Total payload across these three phases: **−12.42%**. Initial loading and
  door preparation are excluded; this is not a cold-session saving estimate.
- Revisit payload: **−29.10%**. This must not be presented as a 29% latency/FPS
  improvement: disk reads, decoding, copies and GPU work remain.
- All 27 modeled phase/policy combinations reproduce the same visible cut:
  7,450,287 facade / 7,453,039 door splats. This is not new pixel-parity evidence.

The 96 MiB policy ends the final speculative batch with 2,174,719,392 unique
decoded bytes, above the persistent cap; terminal eviction is explicitly not
modeled because no later demand is evaluated. The 64 MiB policy ends with
2,118,339,872 bytes. These are cumulative unique pack bytes, **not** measured
RAM/IndexedDB peaks or a claim that the cache ceiling was exceeded at runtime.

Exact numbers, pack plans and fingerprints are in the
[replay result](gstile-prefetch-budget-replay.json).

## Complementary RAM-cache opportunity

The user asked whether a larger RAM cache would help. The same manifest and
visible cuts require 720,733,184 bytes for door, 723,733,824 for facade, and
**1,323,586,240 bytes (1,262.27 MiB, 554 unique packs)** for their union.

| RAM budget | Raw capacity left after both views |
| --- | ---: |
| Current 768 MiB | −494.27 MiB: cannot retain both |
| 1,024 MiB | −238.27 MiB: still insufficient |
| 1,536 MiB | +273.73 MiB |
| 2,048 MiB | +785.73 MiB |

The recorded revisit has 33 RAM hits and 251 IndexedDB hits; its entire
network payload is speculative. A separate **1.5 GiB RAM experiment** is
therefore justified for revisit latency, with up to 768 MiB extra pack-cache
storage. Capacity arithmetic does not prove retention under segmented-LRU
replacement, nor peak browser/process memory safety. The stored packs are
Q96 data, not decoded render streams; increasing this cache would not remove
decoding or GPU work and does not increase VRAM deliberately.

The RAM cap is **not changed by this phase**. Do not infer permission to raise
all browsers' memory allocation from this desktop-specific investigation.
The existing process-memory qualification remains open. A bounded desktop
profile should be qualified separately before making it a default.

## Verification and remaining limits

- Five of 34 focused cases failed with the 96 MiB floor; all 34 pass with 64.
- Full frontend: 380 tests across 41 files pass (nine added cases).
- Typecheck and scoped ESLint pass. Tests retain cold-start behavior, the
  64 MiB exploration floor, low-utility feedback, unchanged higher-utility
  budgets and recovery after previously prefetched bytes become useful.
- The existing 27–29% utility qualification cases still yield 210/222 MiB;
  the original [adaptive-budget evidence](gstile-adaptive-prefetch-budget-qualification.md)
  remains historical evidence for its original revision, not new visual acceptance.
- Normal PR CI is responsible for build and ordinary browser journeys; the
  local served `.next` directory is not overwritten for this phase.

The scheduler's useful-byte signal credits first RAM hits and in-flight
promotion, not later persistent hits whose RAM marker was evicted. That
existing limitation is unchanged. On other paths, a lower floor could defer
useful prefetch and increase later demand latency; lower speculative traffic
alone cannot establish an end-to-end improvement. Browser/GPU latency,
cold-start feedback and process-memory qualification remain open.

Reproduce with Node 24+ and an unused output path:

```sh
node docs/benchmarks/gstile-prefetch-budget-replay.mjs \
  /home/olivier/droneai-qualifications/gstile-cache-path-manual-20260827 \
  /home/olivier/droneAI \
  /tmp/gstile-prefetch-budget-recheck.json
```

Raw data, failed/passed test reports and comparison outputs are retained at
`/home/olivier/droneai-qualifications/gstile-prefetch-budget-20260827` with
Windows staging copies. No browser was controlled, server restarted, cache
cleared or dataset deleted. Keep this phase separate from draft PRs #277/#278;
no production activation until the outstanding qualification is addressed.
Rollback is a code revert, without migration.
