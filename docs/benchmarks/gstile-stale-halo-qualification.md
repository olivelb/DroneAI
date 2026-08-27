# GSTile stale-motion halo budget

2026-08-27. Status: implemented and unit-tested; browser comparison pending.

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

A separate same-memory old/new Chrome cohort will measure readiness, critical
and speculative compressed bytes, and exact final cuts with the unchanged
stabilized AB/BA protocol. It must show a useful improvement and retain operator
visual conformity before merge. The prior RAM cohort's visual approval is not
silently applied to a new loading sequence. Total Chrome memory is not a gate,
per explicit user decision; byte bounds and runtime errors remain checked.

Rollback: revert this phase; no migration or cache reset is needed. Historical
successful and failed cohorts, including their original analyzer verdicts,
remain unchanged.

## Manual comparison prepared

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
tests pass; syntax checks pass. At preparation time this cohort is **not run**.
