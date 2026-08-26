# GSTile persistent-aware prefetch qualification

Date: 2026-08-26

## Objective

Prevent immutable ranges already stored in the browser persistent cache from
consuming the speculative prefetch transfer budget. Inventory must not read or
decode cached blobs, and superseded camera planning must remain cancellable
before it starts network transfers. The visible LOD cut and its quality are
unchanged.

## Implementation

- Persistent availability is queried in one read-only IndexedDB metadata scan
  over the access store; cached blob values are not loaded.
- Hits require the exact immutable SHA-256/range key and expected byte length.
- Memory and in-flight availability is combined with the persistent inventory
  before applying the existing 384 MiB speculative transfer ceiling.
- A camera interaction owns one abort signal covering persistent inventory,
  planning, and speculative requests. A newer interaction cancels the entire
  obsolete sequence.
- Persistent inventory failures remain advisory, while aborts propagate and do
  not count as cache errors.
- Telemetry records query, candidate, and hit counts without changing LRU order.

## Qualification target

- Scene: Saint-Etienne facade, merged rendering.
- Bundle: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
- Pack layout: 2 MiB depth-spatial aggregation.
- Browser: Chrome on the qualification workstation.
- Server: production Next.js build on BIGZEN, bundle served from BIGZEN `I:`.

## Automated contracts

- TypeScript typecheck passed.
- 63 focused GSTile tests passed.
- Production Next.js builds passed locally and on BIGZEN.
- Targeted lint passed with no new warning. Two unrelated pre-existing unused
  variable warnings remain in `playcanvas-backend.ts`.

Tests cover exact immutable identity and byte-length matching, batched metadata
inventory, memory/in-flight union, advisory persistent failures, and aborting a
blocked persistent inventory when camera planning is superseded.

## Runtime observations

After the visible load and speculative completion, the planner reported 383
locally available packs representing 809,258,656 bytes. It skipped 10 already
available candidates representing 7,985,024 bytes and completed 136 absent
packs representing 402,653,120 bytes under the unchanged budget.

The scheduler performed one persistent availability query over 2,174 candidate
packs and found three exact persistent hits without loading their blobs. It
reported zero persistent-cache errors and no remaining active or queued range
requests. The observed visible update took 2,324.2 ms, including 1,740.9 ms of
range loading, in the evolving browser-cache state used for visual
qualification.

These observations validate metadata-only persistent discovery, budget
reassignment, and completion. They are not a randomized cold-cache A/B estimate
and therefore do not isolate a causal latency gain from prior cache and prefetch
optimizations.

## Visual acceptance

The user validated conformity with the original PLY on the persistent-aware
qualification page. No visual-quality change was reported.
