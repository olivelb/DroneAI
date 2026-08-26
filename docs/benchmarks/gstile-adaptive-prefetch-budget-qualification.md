# GSTile adaptive prefetch budget qualification

Date: 2026-08-26

## Objective

Reduce speculative network traffic when completed prefetch ranges are rarely
consumed by later visible cuts. The controller must never alter the visible
cut, its priority, Gaussian budget, decoded representation, or rendering.

## Policy

- Cold-start maximum: 384 MiB.
- Minimum adaptive budget: 96 MiB.
- Minimum completed-prefetch sample before adaptation: 128 MiB.
- Target useful-byte ratio: 50%.
- Budget formula: `384 MiB * observed utility / 50%`, clamped to
  `[96 MiB, 384 MiB]` and quantized to whole MiB.
- Utility is based on the existing first-use prefetch promotion counters.
- The cumulative session signal avoids reacting to one pack or one frame.
- Only speculative planning receives the result; visible requests remain
  critical and unconstrained by this budget.

## Qualification target

- Scene: Saint-Etienne facade, merged rendering.
- Bundle: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
- Pack layout: 2 MiB depth-spatial aggregation.
- Browser: Chrome on the qualification workstation.
- Server: production Next.js build on BIGZEN, bundle served from BIGZEN `I:`.

## Automated contracts

- TypeScript typecheck passed.
- 67 focused GSTile tests passed.
- Production Next.js builds passed locally and on BIGZEN.
- Targeted lint passed with no new warning. Two unrelated pre-existing unused
  variable warnings remain in `playcanvas-backend.ts`.

Tests cover cold-start sampling, the measured Saint-Etienne cohort, strict
minimum and maximum bounds, and inconsistent-counter rejection.

## Runtime observations

The initial plan correctly retained the 384 MiB cold-start budget and completed
145 speculative packs representing 402,648,032 decoded bytes and 350,227,986
network bytes.

After repeated door-to-facade navigation, the planning snapshot observed
27.38% useful bytes and selected a 210 MiB adaptive ceiling. The cumulative
session counters later reported 247 completed prefetch requests and 67 useful
requests: 791,014,880 completed bytes, 213,058,304 useful bytes, and 689,898,002
prefetch network bytes. The final plan required only three absent packs totaling
12,931,776 bytes because local inventory already covered most candidates.

The final visible update took 107.0 ms: 63.4 ms loading and 42.9 ms commit. The
scheduler had no active or queued request, no persistent error, and the plan had
no prefetch error.

These are sequential warm-cache observations. They validate controller
activation, bounds, and interoperability with the cache-aware planner; they do
not isolate a causal latency improvement from accumulated cache state.

## Visual acceptance

The user validated conformity with the original PLY after repeated navigation
with the adaptive controller active. No visual-quality change was reported.
