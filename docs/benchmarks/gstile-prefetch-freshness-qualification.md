# GSTile camera-prediction freshness — offline qualification

Date: 2026-08-27. Base: `15758bd2bfffa0be821b51b4a5d1356d5b1415cf`
(`codex/gstile-cache-isolation`, draft PR #277). This is a separate correction,
not an extension of the accepted camera benchmark to a new runtime build.

## Finding and change

Camera velocity is updated on camera changes, not on stationary frames. The
prefetch timer runs 600 ms after a completed visible-cut commit. Previously,
`predictGsTileCameraPose` extrapolated the last velocity indefinitely when no
new camera event arrived. Resetting velocity on the *next* widely spaced
sample did not protect this idle path.

The predictor now requires a timestamp from the same monotonic clock. An
estimate whose age reaches its prediction horizon (currently 1,500 ms) returns
null. Future-clock samples also return null; non-finite query timestamps are
rejected. Fresh estimates retain exactly their previous full lookahead,
smoothing, translation and angle bounds. This validity window is an explicit
policy, not a claim that 1,500 ms is optimal for every interaction.

The backend supplies `performance.now()` at planning time and exposes
`performance.lodPrefetch.motionAgeMs` alongside the existing prediction
activity/speed fields. Returning null skips one speculative LOD traversal and
its candidate list. Stationary halo prefetch remains active. Visible-cut
selection, Q96 decoding, SH/directional opacity, shaders, GPU assembly,
transport/integrity checks and cache capacities are unchanged.

## Evidence and negative result

Source cohort: `gstile-cache-path-manual-20260827`, described in the
[cache-isolation qualification](gstile-cache-isolation-qualification.md).
All three candidate arms are included (warmup plus both measured pairs),
three phases each. The baseline implementation in these traces is `9ee1bb5`.
No browser, GPU, server or new transfer was used for this analysis.

All nine snapshots reported an active prediction despite the final input
already being 1,561–3,260 ms old at the cut commit, **before** the prefetch
timer. Including its nominal delay gives 2,161–3,860 ms; these are not directly
measured timer firing timestamps. Both bounds establish stale motion relative
to the 1,500 ms horizon.

The replay reconstructs local pack availability from the completed Zstd
request history before each prefetch batch. It checks the reconstructed pack
count and decoded bytes against the recorded availability telemetry, the
compression lengths against the manifest, and phase bytes against scheduler
counters. There were no repeated network requests within these candidate
phases relative to earlier phases. Cache churn/re-downloads are therefore not
the cause of the observed candidate revisit traffic.

At fixed recorded cache availability and budget, recomputing only the
stationary halo gives the following **one-step counterfactual**, not a newly
measured whole-path result. MB below means decimal compressed payload bytes.

| Measured arm / phase | Observed prefetch | Stationary-only plan | Planned packs, old → new |
| --- | ---: | ---: | ---: |
| Pair 1 / first door → facade | 75.945 MB | 76.014 MB | 32 → 41 |
| Pair 1 / return facade → door | 77.130 MB | 77.130 MB | 23 → 23 |
| Pair 1 / revisit door → facade | 75.510 MB | 76.053 MB | 33 → 44 |
| Pair 2 / first door → facade | 75.945 MB | 76.014 MB | 32 → 41 |
| Pair 2 / return facade → door | 77.130 MB | 77.130 MB | 23 → 23 |
| Pair 2 / revisit door → facade | 75.638 MB | 76.053 MB | 34 → 44 |

**This change is not a demonstrated bandwidth optimization.** The 96 MiB
decoded-byte budget floor still fills with other halo candidates. Revisit
compressed traffic is slightly higher in the fixed-state counterfactual
(approximately +0.72% / +0.55%) and request count is higher. Keep the freshness
fix for correct prediction semantics and the avoided traversal; do not market
it as a loading-speed improvement or promote it on that basis.

Neither the observed post-commit prefetch packs nor the counterfactual packs
intersect the next recorded settled visible cut in any of the six phases
with a recorded successor. This says nothing about usefulness after the final
phase or for other trajectories. The next bandwidth experiment should target
the forced 96 MiB floor / marginal prefetch utility. Reducing that floor is
**not implemented here**: changed early prefetch alters later cache state and
utility counters, so the one-step replay cannot qualify a whole-path policy.

## Verification and reproduction

- Regression first: 8 of 25 focused tests failed against the old predictor
  (expiry, future timestamp and non-finite clock); all 25 pass after correction.
- Full frontend: 371 tests across 41 files pass (12 added cases).
- `npm run typecheck` and ESLint on the three modified TS files pass.
- Offline replay: all nine visible node-id sets and resident counts exactly
  match the snapshots (7,450,287 facade / 7,453,039 door splats). This is a CPU
  selection check, **not** a new pixel-parity or GPU timing measurement.
- Production build is delegated to the normal frontend PR CI to avoid
  overwriting an existing served `.next` directory. Its result is reported
  separately in the PR.

The [replay program](gstile-prefetch-freshness-replay.mjs) requires Node 24+
for native TypeScript stripping and reads only local files. The
[recorded replay result](gstile-prefetch-freshness-replay.json) includes source,
script and input SHA-256 hashes, exact pack plans and all nine results. It
does not contain the signed pack URLs from the retained descriptor. A Node
module-type warning is expected for these TypeScript modules; package module
semantics were not changed just to silence a research script warning.

From the authoritative repository, using a new output filename:

```sh
node docs/benchmarks/gstile-prefetch-freshness-replay.mjs \
  /home/olivier/droneai-qualifications/gstile-cache-path-manual-20260827 \
  /home/olivier/droneAI \
  /tmp/gstile-prefetch-freshness-recheck.json
```

The script rejects incomplete transfers, incompatible cohort shape, integrity
or transport inconsistencies and mismatched availability instead of silently
producing estimates. It deliberately does not simulate later eviction,
modified utility feedback, IndexedDB latency, GPU execution or browser memory.

## Delivery limits

This phase remains separate from PR #277. The later
[complete RAM cohort](gstile-memory-profile-qualification.md) includes this
correction in both arms and received operator visual approval on 2026-08-27;
it cannot isolate the correction's individual speedup. The user retired total
Chrome process-memory testing as a merge prerequisite and authorized delivery
with green CI. No universal speedup or total-memory qualification is claimed.
All earlier accepted and failed runs are retained; no caches or datasets were
deleted. Rollback is a code revert with no migration.
