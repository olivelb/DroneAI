# GSTile prefetch utility telemetry qualification

Date: 2026-08-26

## Objective

Measure whether speculative GSTile transfers are subsequently required by a
visible cut. The signal is intended to support an adaptive prefetch budget
without changing the selected cut, decoded representation, or rendering.

## Implementation

- Every range keeps its initial `critical` or `prefetch` provenance even when
  an in-flight request is promoted.
- Completed prefetch requests report decoded bytes and distinguish network
  bytes from persistent-cache bytes.
- A memory-resident prefetched range is counted as useful only on its first
  critical cache hit.
- An in-flight prefetch is counted as useful when its first critical consumer
  promotes it.
- Prefetch provenance is removed on first use, replacement, or LRU eviction,
  preventing duplicate utility credit and stale markers.
- Counters are constant-time updates on existing scheduler transitions; no
  per-frame scan or additional network request is introduced.

## Qualification target

- Scene: Saint-Etienne facade, merged rendering.
- Bundle: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
- Pack layout: 2 MiB depth-spatial aggregation.
- Browser: Chrome on the qualification workstation.
- Server: production Next.js build on BIGZEN, bundle served from BIGZEN `I:`.

## Automated contracts

- TypeScript typecheck passed.
- 64 focused GSTile tests passed.
- Targeted ESLint passed without warning.
- Production Next.js builds passed locally and on BIGZEN.

Tests prove that both queued/in-flight promotion and a completed memory-cache
promotion are credited. A repeated visible cache hit does not receive a second
credit.

## Initial runtime observation

After the initial visible load and speculative completion, 141 prefetch
requests had completed, representing 402,576,384 decoded bytes and 349,950,891
network bytes. No range was useful yet because no subsequent camera move had
occurred at the sampling point. The scheduler also reported 165 persistent
cache hits, 358 zstd responses, zero zstd fallback, and zero persistent error.

The encoded-to-decoded difference for the speculative cohort was approximately
13.1%. This is one evolving warm-cache observation, not a controlled estimate
of the corpus-wide compression ratio. A later camera movement is required to
measure utility.

## Visual acceptance

The user validated conformity with the original PLY on the instrumented build.
No visual-quality change was reported.
