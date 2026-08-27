# GSTile — aligned staging integration, 2026-08-27

Baseline: `fdc2b042b8075809cc75dc54262fc9ed5c2d7ae1` (PR #274).
Implementation: `a96885476f1fc81b4e5db122d6190b745eb61de5`.
Final telemetry correction: `0627d65516cc585518af21784ee612606c48f6ba`.
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

The prototype's 16–20% host gain must not be represented as measured renderer
FPS or full loading acceleration. Human PLY acceptance of this revision is
**pending**; the PR remains open until that gate is met.

Evidence directory (retain all sources, raw results, failed attempts and logs):
`/home/olivier/droneai-qualifications/gstile-aligned-integration-20260827`.

## Real Worker and PlayCanvas qualification

Windows Chrome 151, RTX 4070 Laptop (Ada), pinned PlayCanvas 2.21.4, WSL Node
24.14.0 on the same i9-13900H qualification workstation. Exact TypeScript and
compiled module hashes plus the engine bundle hash are retained. A standalone
page executes the real assembly Worker/client and actual PlayCanvas
GSplatResource/GSplatContainer/Texture objects, not mock texture adapters.

The 1,288,047-record fixture has five input ranges (four 300,000-record inputs
and one 88,047 input), staging 2,739 × 471 and arena 2,739 × 2,739. Finite
deterministic typed values are generated per input; native input padding is
discarded during assembly. All twelve transferred buffers match an independent
main-thread assembler bit-for-bit. Ownership detaches on transfer. Resource
construction checks color→transform→SH order, aligned dimensions and native
array identity, including the four extra opacity streams.

Every full destination texture is read back and hashed against a CPU scatter
oracle, including gaps and tail. Two consecutive updates use different data,
so an omitted second write cannot pass by retaining the first correct payload.
All **22 GPU texture hashes and 12 Worker buffer hashes pass**; each update
issues 253 texture copies. An independent Node validator reconstructs the
fixture, hashes, ranges, counters and module provenance. No GPU validation errors.
Actual Worker abort settles; a manual main-thread retry preserves the layout.
Automatic backend failure recovery remains covered by existing unit contracts,
not a new fault-injected full-viewer failure.

The first two harness attempts read zeros because the engine's default deferred
readback relies on a render-loop submission. This isolated page has no such
loop. The corrected harness uses `Texture.read(..., {immediate: true})`; all
failed sources/results are retained. No renderer copy logic or hash tolerance
was changed to make these checks pass.

## Saint-Etienne integration checks

The unchanged bundle is
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Initial view, camera Shift-drag, assembly-disabled reload and normal reload all
settle with one merged entity/resource, 11 streams, zero pending nodes, zero
decoder fallbacks and zero assembly-worker failures. World-active count equals
resident count. Initial/fallback/reloaded cuts preserve exactly the baseline's
374 selected IDs and 7,461,366 splats. The gesture settles at 7,447,130 splats,
adding 451,363 and reusing 6,995,767. This is a functional gesture, not a frozen
door→facade benchmark or a new PLY metric/visual acceptance.

Debug capture and independent index-map validation confirm actual disjoint
copies and equal staging/arena widths. The first large refinement adds
7,265,600 records across 365 spans and issues **18,447 copies**. The gesture
adds 451,363 across 45 spans and issues **1,958 copies**. Replaying the **old
planner** with square staging and these same captured ranges yields 62,821 and
9,702 copies respectively. Those old counts are recalculated, not instrumented
observations from the old deployed binary. No downloaded bytes are reduced.

`gstileWorkerAssembly=0` explicitly tests main-thread assembly with decoder
Workers still enabled: assembly bytes become zero and output-copy bytes become
1,249,683,200, with unchanged cut and copy count. It is not a test without all
Workers or a simulated Worker crash.

## Telemetry consistency bug discovered during measurement

The backend renders synchronously while committing a cut. That render could
publish the new load/range state with the preceding cut's commit/total timings,
then the one-second debug throttle delayed correction. An initial alternating
six-pair reload experiment captured **9 incoherent snapshots among 14 arms**
(including warmups): total LOD time was shorter than load plus commit.

The entire first timing series is rejected, not filtered for favorable samples.
Its raw JSON, computed preliminary summary, coherence audit and commands remain
retained; the preliminary `paired-validated.json` is **not valid performance
evidence**. Exact cut/count checks from that series remain valid.

The fix forces the final debug snapshot immediately after commit/total timings
are assigned, bypassing only the debug throttle at this completion boundary.
It changes neither GPU commands nor Worker modules. The preceding GPU hashes
therefore still apply to those identical modules. This also prevents future
profiling from mixing generations. Full-frame/input-latency tails, other GPUs
or browsers and original-PLY visual acceptance remain unqualified.

Four new captures on `0627d65` (initial, pan, assembly disabled, normal reload)
pass independent final-snapshot checks: DOM attributes match JSON timings,
total is at least load plus commit, no pending/failing tasks, and every copy
covers the expected indices exactly once. The three initial-view cuts match
the baseline IDs/count. The final pan reaches the same 7,447,130-splat cut via
a smaller last update (220,758 added, 7,226,372 reused, 16 spans, 825 copies).
This is not the same last-update workload as the preceding gesture; its timing
must not be compared to that gesture as an A/B performance result.

Small updates remain below the existing 2M-record assembly-Worker admission
threshold and use main-thread assembly with decoder Workers. The first version
of the final-capture validator incorrectly required assembly-Worker bytes for
this small pan. Its source/failure log are retained; the corrected assertion
checks the threshold and expected main-thread output bytes instead. This is
not a renderer failure or a Worker fallback.
The second validator attempt confused `assemblyWorkerDisabled` (failure-induced
unavailability) with the URL configuration. Its failure is retained too; the
final check verifies the URL switch separately and requires no failure-induced
disablement in all four captures.

## Served candidate and rollback

Final image `droneai-frontend:0627d65`, image ID
`sha256:ac2696b22b5a3a660be5e2126fc14c55582d818173f82c04938234f830ed0b87`,
is running as `droneai-frontend-gstile-0627d65` on BIGZEN, loopback port 3000,
with restart-unless-stopped. Revision label identifies the full runtime commit
above. Its exact source archive SHA256 is
`bf007d9bed8b6787e00f62b25d0236517072dca388214fbcee13a73ad45b34b2`.
The follow-up documentation commit does not change deployed runtime sources.

The accepted `droneai-frontend-gstile-69e848c` container/image and intermediate
`droneai-frontend-gstile-a968854` remain stopped and retained. Deployment scripts
check exact identities and restore the preceding service on failed bounded
health checks; that failure branch was not fault-injected. Manual rollback is
to stop only `droneai-frontend-gstile-0627d65`, start the retained accepted
`droneai-frontend-gstile-69e848c`, verify HTTP readiness, then reload the page.
No bundle, cache, historical evidence, image or container was deleted.

Before merge: user checks the original PLY appearance in the normal merged
viewer, including close door/facade movement; required PR CI must remain green.
This batch establishes exact layout/copy behavior, not a universal browser/GPU
qualification, network improvement, or production-wide renderer speedup.
