# GSTile camera-motion prefetch qualification

Date: 2026-08-26

## Objective

Reduce the latency of consecutive camera moves without changing the selected
visible cut, its Gaussian budget, or the decoded representation. Camera-motion
prefetch remains speculative and lower priority than every visible-cut range
request.

## Implementation bounds

- Exponentially smoothed camera position, direction, and up-vector velocity.
- Samples more than 2,000 ms apart start a new estimate.
- Prediction horizon: 1,500 ms.
- Maximum predicted translation: 75% of the current orbit distance.
- Maximum predicted angular displacement: 35 degrees.
- Shared prefetch transfer ceiling: 384 MiB; prediction does not increase it.
- A new pointer gesture resets the previous motion estimate.

## Qualification target

- Scene: Saint-Etienne facade, merged rendering.
- Bundle: `sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
- Pack layout: 2 MiB depth-spatial aggregation.
- Browser: Chrome on the qualification workstation.
- Server: production Next.js build on BIGZEN, bundle served from BIGZEN `I:`.

## Automated contracts

- TypeScript typecheck passed.
- 59 focused GSTile tests passed.
- Production Next.js build passed locally and on BIGZEN.
- Targeted lint passed with no new warning. Two unrelated pre-existing unused
  variable warnings remain in `playcanvas-backend.ts`.

The motion tests cover velocity smoothing, stationary and stale samples, slow
pan retention, translation caps, angular caps, and normalized directions.

## Runtime observations

A controlled slow pan produced four usable motion samples. The predicted cut
contained 967 candidate nodes. The existing 384 MiB ceiling selected 133 packs,
all attributed to the predicted direction; 114 completed before the next
measurement and 19 obsolete prefetch transfers were cancelled.

A subsequent same-direction gesture completed its visible LOD update in
168.8 ms: 117.4 ms load and 46.1 ms commit. At that point the scheduler reported
790 memory-cache hits, 27 in-flight hits, and 12,071,041 cumulative network
bytes for the browser session.

These are warm-cache sequential observations, not a randomized cold-cache A/B
estimate. They demonstrate that bounded trajectory planning activates, fills
only the existing budget, and can feed a continuation. They do not isolate its
causal speedup from the persistent cache or the prior halo prefetch.

## Visual acceptance

The user validated conformity with the original PLY after testing consecutive
same-direction moves and abrupt direction changes. No visual-quality change was
reported.
