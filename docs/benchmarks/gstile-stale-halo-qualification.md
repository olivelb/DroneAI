# GSTile stale-motion halo budget

2026-08-27. Status: complete six-run Chrome comparison accepted; visual
conformity to the PLY confirmed by the operator. Ready for delivery with green CI.

## Observation and change

The [completed RAM pilot](gstile-memory-profile-qualification.md) improves
post-input latency by 13–21%, but a larger cache retains more historical
prefetch-usefulness credit. Even after camera motion expires, that history
funds large stationary halos without newly useful prefetch in the measured
gestures. The 1,536 MiB arm uses 156 / 126 / 109 MiB budgets.

After at least 128 MiB of completed prefetch, stale motion (last sample at least
1,500 ms old) now caps the speculative halo at the existing 64 MiB exploration
floor. A missing motion sample or a fresh sample retains the original policy.
Cold exploration below the sample threshold retains 384 MiB. New camera input
immediately restores the normal utility-based budget; there is no sticky state.
`performance.lodPrefetch.staleMotionCap` reports application of the cap.

No cut selection, demand request, cache capacity, pack, decoder, shader or GPU
stream changes. This does not fix attribution of useful persistent-cache hits
after their RAM marker is evicted; that is a separate follow-up. The cap is per
halo plan, not a whole-session traffic limit.

## Evidence and limits

Six regression cases cover the observed counters, threshold boundary, fresh
motion recovery and invalid counters. Five failed before implementation.
All 40 prefetch tests and all **396 frontend tests / 42 files** pass. TypeScript,
scoped ESLint and diff whitespace checks pass. Required PR CI supplies the build;
the existing served application is not rebuilt in place.

For the three recorded candidate states the allowed raw budgets fall from a
sum of 391 MiB to 192 MiB (−50.90%). This is a **policy calculation, not measured
network savings or latency**. The new policy also changes preparation, eviction
and later usefulness. Replaying only the historical download union cannot
reconstruct IndexedDB evictions, so no full-path counterfactual is claimed.

The separate same-memory old/new Chrome cohort measured readiness, critical
and speculative compressed bytes, and exact final cuts with the unchanged
stabilized AB/BA protocol. It passed all frozen tolerances and received its own
operator visual approval. Total Chrome memory is not a gate, per explicit user
decision; byte bounds and runtime errors remain checked.

## Completed result

Both measured pairs have identical payload totals per arm. Over the three
gestures, compressed network bytes fall from **428,965,262 to 269,085,824
(−37.27%)**. Speculative bytes fall from **311,143,606 to 152,753,618 (−50.91%)**.
Including initialization and door preparation, each complete passage falls
from 1,850,645,135 bytes / 974 responses to 1,531,744,645 / 854 (−17.23% bytes).
The same counts hold for both warmups, which are excluded from timing medians.
The server log reconciles exactly with every browser result; no partial or
unattributed transfer is present.

| Gesture | Old median after-input ms | Capped median ms | Old compressed bytes | Capped bytes |
| --- | ---: | ---: | ---: | ---: |
| First door → facade | 2,592.15 | 2,572.50 | 239,810,647 | 167,138,249 |
| Return facade → door | 1,264.00 | 1,238.85 | 102,894,388 | 51,364,484 |
| Revisit door → facade | 1,350.30 | 1,347.20 | 86,260,227 | 50,583,091 |

The two-pair pilot establishes reduced traffic with readiness non-regression,
**not a meaningful additional latency speedup**. Pair 1 first transition is
30.9 ms slower and pair 2 revisit is 39.6 ms slower; both remain within frozen
tolerances. One main-thread long task per gesture remains (113–139 ms in the
measured pairs). No smoothness improvement is established.

The first transition retains the same 116,332,206 demand bytes. Return demand
falls from 1,489,450 bytes to zero; revisit demand remains zero. Return and
revisit both retain zero IndexedDB reads. Every candidate measured halo reports
the 64 MiB stale-motion cap. Raw-cache peaks stay below 1,536 MiB and the logical
GPU peak stays about 2.112 GB; these are not total process or physical VRAM.
All six runs, twelve cadence controls, stabilizations, foreground checks, runtime
checks and exact final-cut comparisons pass. Operator visual approval is for
this cohort, not inherited from the previous RAM experiment.

The immutable `analysis-completed.json` reports `complete: true`,
`pilotWithinTolerances: true`, no failures; total process peak remains unmeasured.
SHA-256: `ee3769a55c8f437109632216c52d4c5d7713a029b095ce12c3822b5353172013`.
Frozen manifest SHA-256:
`8ed7a3c02f2b89e882a7b8ade0080e6cf0d96d6d7a48bc2a6a6659ea58967697`.
`audit-network.mjs` and `audit-network.json` are retained alongside all six raw
results, hashes, controls and network logs in the evidence directory below.
No original result or previous failed verdict was replaced.

Rollback: revert this phase; no migration or cache reset is needed. Historical
successful and failed cohorts, including their original analyzer verdicts,
remain unchanged.

## Manual comparison and reproduction

The shared [manual harness](gstile-memory-profile-manual/) now supports two
explicit protocols: same-code RAM comparison and same-memory stale-halo
comparison. The latter requires distinct pinned revisions and rejects every
runtime difference except `lod-prefetch.ts` and `playcanvas-backend.ts`.
Unchanged modules must also have identical instrumentation and compiled hashes.
The engine is shared and hashed. Existing frozen cohorts are not modified.

Reference `b394f0e3ec5e2c26b099bf475b2a898a223be906`; candidate
`5b1e290c4713de84444ab90c89febdd42d5a4eeb`. Both use desktop/1,536 MiB.
Copy the shared sources to a new evidence directory and copy
`stale-halo.protocol.json` to `protocol.json`, then run:

```sh
node --test camera.test.mjs controls.test.mjs profile.test.mjs idle-window.test.mjs
node prepare.mjs
node server.mjs
# After descriptor capture, from another terminal:
node freeze.mjs
# Operator starts once in Chrome; after all six passages:
node analyze.mjs analysis-completed.json
```

Directory: `/home/olivier/droneai-qualifications/gstile-stale-halo-20260827`.
URL: `http://127.0.0.1:3030/`. Fresh per-passage IndexedDB databases, two warmups
then two AB/BA pairs, fixed five-second stabilization, unchanged cadence limits,
manual start. The analyzer checks cap telemetry as well as final cuts, errors,
latency and network. It reports speculative bytes separately. Twelve harness
tests pass; syntax checks pass. This cohort is now complete; reproduce into a
new directory/origin/database prefix, never overwrite its results.
