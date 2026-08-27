# GSTile — aligned staging integration, 2026-08-27

Baseline: `fdc2b042b8075809cc75dc54262fc9ed5c2d7ae1` (PR #274).
This implements the bounded follow-up to the
[isolated experiment](ALIGNED_STAGING_EVALUATION_20260827.md).

## Implementation

Incremental merged staging now uses the existing arena width. Column allocation,
Worker init/finish validation, memory accounting and empty engine stream resize
carry the same explicit width. Individual decoded tiles and newly promoted
persistent arenas retain square packing. Arrays are allocated in the final shape;
there is no repadding copy on the main thread. Bounds/centers, coefficients,
precision, cut policy, shaders, transport and bundle are unchanged.

Equal-width linear ranges use at most six disjoint rectangles. Different widths
retain the original row-split planner. The shared helper still plans once per
shape and preserves stream/range order; it now returns the issued copy count.
Debug-only `performance.lodArenaCopies` retains the last commit's actual ranges,
dimensions, stream/copy counts and host copy-plus-centers duration. It adds no GPU
fence. This timing is not a GPU timestamp or a complete commit duration.

The twelve-owned-buffer invariant, four-task/128 MiB decode admission and 2 GiB
Worker destination budget remain; the latter includes actual padding. Missing
or mismatched Worker result width is rejected. Worker failure retains the existing
one-time pre-commit retry using main-thread assembly with the same layout.

## Local checks and remaining qualification

336 frontend tests, typecheck and targeted lint pass. The initial 13 regression
tests fail against the reference, then pass after integration; two more cover
engine resize idempotence and scalar decode parity. Tests cover exact active bytes
and zero padding, out-of-order assembly, default layout, invalid/overflowing widths,
budget padding, returned Worker shape and disjoint linear mapping.

Target-engine/GPU, production build, actual viewer transitions and human PLY
acceptance are recorded below when completed. The prototype's 16–20% host gain
must not be represented as measured renderer FPS or full loading acceleration.

Evidence directory (retain all sources, raw results, failed attempts and logs):
`/home/olivier/droneai-qualifications/gstile-aligned-integration-20260827`.
