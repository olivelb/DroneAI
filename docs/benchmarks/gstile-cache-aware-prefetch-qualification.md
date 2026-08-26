# GSTile cache-aware prefetch qualification

Date: 2026-08-26

## Objective

Prevent packs already present in the scheduler memory cache, or already in
flight under the same immutable identity, from consuming the speculative
prefetch transfer budget. The visible LOD cut and its quality are unchanged.

## Implementation

- Local availability is keyed by immutable SHA-256 identity and exact byte
  range, so signed-URL rotation cannot create a false cache miss.
- Locally available packs are removed before the 384 MiB ceiling is applied.
- Freed capacity is spent on later, absent packs in the existing screen and
  predicted-camera priority order.
- Cache inspection does not update LRU order or cache counters.
- Telemetry reports local pack inventory and candidate packs skipped.

## Qualification target

- Scene: Saint-Etienne facade, merged rendering.
- Bundle: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
- Pack layout: 2 MiB depth-spatial aggregation.
- Browser: Chrome on the qualification workstation.
- Server: production Next.js build on BIGZEN.

## Automated contracts

- TypeScript typecheck passed.
- 61 focused GSTile tests passed.
- Targeted lint passed with no new warning.
- Production Next.js builds passed locally and on BIGZEN.

Tests prove that an immutable range is reported while in flight and after
memory admission, but not under another identity. A planner test proves that a
cached 200-byte pack is skipped and its capacity is reassigned to the next
150-byte and 100-byte candidates under an unchanged 250-byte ceiling.

## Runtime observations

After the visible cut, the scheduler held 240 entries and 466,798,080 decoded
bytes. Before the first measured speculative plan it recognized 380 locally
available packs representing 804,539,968 decoded bytes. The planner still
selected 136 absent packs totaling 402,653,120 decoded bytes, demonstrating
that local inventory no longer consumes the transfer ceiling.

After a camera move, 315 locally available packs represented 804,944,928 bytes
and the next plan selected 138 absent packs totaling 402,652,800 bytes. These
measurements validate budget reassignment, not an isolated latency speedup;
network and persistent-cache state evolved during the sequence.

## Visual acceptance

The user validated conformity with the original PLY after repeated navigation
between distant facade regions. No visual-quality change was reported.
