# GSTile — shared texture-copy planning, 2026-08-27

## Decision and scope

Baseline: `f350d4a35a6333f956925958c40ef3d9c1602cfb` (clean WSL checkout).
Implementation: `69e848c072e18bdc7f214fc0ac01fbaf35a88b51`.

Keep one row-copy plan per linear range and reuse it across successive streams
with the same source width/height and destination width/height. In the normal
eleven-stream packed path this removes ten redundant planning passes per range.
Any dimension change calls the original validated planner again. Only the last
shape is retained, within the synchronous range call; no persistent/global cache,
cross-cut reuse, extra worker or configuration switch is introduced.

The existing orchestration is extracted into `arena-texture-copy.ts` so the actual
function used by the backend can be tested independently of engine construction.
Stream order, rectangle order, source/destination texture identity, offsets,
copy count and error behavior remain unchanged. Missing streams and rejected
copies still throw by name; a later failure is not a transactional rollback of
earlier GPU commands. PlayCanvas 2.21.4 reads the options synchronously without
mutating them; this pinned adapter contract matters when upgrading the engine.

No shader, numeric packing, SH, opacity, centers, bounds, sort, culling, cut,
network, bundle, tiler or quality-setting change. The rejected direct-upload path
remains disabled/unimplemented in the application. This optimization keeps the
existing staging textures and exact texture-to-texture copies.

**Benefit is small:** approximately 0.9–1.0 ms less CPU geometry/dispatch work
in the synthetic 7.27M update. This is not a meaningful end-to-end loading or FPS
gain by itself. It is retained as a bounded, exact reduction in repeated work,
not as a solution to network latency or large rendering pauses.

## CPU protocol and results

Windows Chrome 151.0.0.0 on the same i9-13900H / RTX 4070 Laptop workstation;
GPU adapter reports NVIDIA Lovelace, Windows driver previously recorded as
32.0.16.1062. WSL Node 24.14.0; PlayCanvas 2.21.4, Vitest 4.1.10, Next 16.3.1.
No process RSS or VRAM peak is measured by this lot.

Reference is the old copying method extracted verbatim from the baseline backend,
with only its declaration changed for standalone compilation. Candidate is the
exact new helper. Both import the same unchanged planner. Original TypeScript,
compiled modules and source/module SHA-256 provenance are retained and validated.

Synthetic deterministic ranges, modeled on the observed update sizes, not a
replay of Saint-Etienne slot allocation: 16,384–32,767 records per range, starting
at destination 17 with 13-record gaps every eight ranges. Source dimensions are
ceil-square packing; destination is 2,739 square. Eleven equally-shaped streams.
Every emitted copy is consumed by a checksum/count sink, **not a GPU call**.

Two separate page runs. Per size/run: two warmups per arm, twelve alternating
AB/BA pairs, eight iterations per arm, 20 ms between arms. Times below are
normalized medians per update; paired percentages are medians of ratios, not
ratios of medians. No forced GC or discarded outliers. Small timings are near
browser clock resolution; do not overinterpret their percentages.

| Records | Run | Reference / candidate ms | Paired change | Planner calls before / after |
|---|---:|---:|---:|---:|
| 128,047 | 1 | 0.100 / 0.025 | -69.05% | 55 / 5 |
| 128,047 | 2 | 0.100 / 0.025 | -73.21% | 55 / 5 |
| 1,288,047 | 1 | 0.381 / 0.175 | -50.25% | 572 / 52 |
| 1,288,047 | 2 | 0.475 / 0.206 | -61.03% | 572 / 52 |
| 7,265,600 | 1 | 1.731 / 0.819 | -51.73% | 3,267 / 297 |
| 7,265,600 | 2 | 1.825 / 0.844 | -53.36% | 3,267 / 297 |

All command counts/checksums match. Calls to the GPU would remain 4,488, 18,216
and 62,062 respectively: this change reduces planning, **not GPU copy volume**.

## Real GPU qualification

The same compiled helpers are used through thin WebGPU texture-copy adapters,
with COPY_SRC, COPY_DST, TEXTURE_BINDING and RENDER_ATTACHMENT usages matching
the installed engine. Two RGBA16F, five RGBA32UI and four RGBA32F streams; finite
float/half and deterministic integer bit patterns. Source uploads are outside
timing. Destinations persist but are fully cleared and fenced **before every
arm**, so previously correct data cannot conceal a missed write.

Each size: two warmups per arm and six alternating AB/BA pairs, 100 ms between
arms. Host timing includes planning, encoding and submission; completion extends
through `queue.onSubmittedWorkDone()`, not hardware timestamps. Readback/hash
work is outside timing. No rendering or PlayCanvas resource construction is
included. The larger run overlaps a remote BIGZEN build, not a local build/viewer;
OS scheduling, clocks, garbage collection and system activity are uncontrolled.

| Records | Host reference / candidate ms | Paired host change | Complete reference / candidate ms | Paired completion change |
|---|---:|---:|---:|---:|
| 1,288,047 | 20.65 / 19.70 | -6.59% | 110.30 / 104.45 | +0.87% |
| 7,265,600 | 65.65 / 64.75 | -1.32% | 376.65 / 374.25 | -0.74% |

The host saving is small and queue completion essentially unchanged. These
component results do not establish a frame-time, input-latency or full-commit gain.

Every arm reads back all eleven complete destination textures with aligned row
pitches, removes padding and compares SHA-256 against a CPU scatter oracle,
including all gaps/tail. Source hashes are checked before/after each series.
All **352 texture hashes** (32 arms including warmups) match. GPU validation
errors fail the run. An independent Node validator reconstructs all expected
source/destination hashes and verifies ranges, counters, sample order, statistics
and module provenance. These are two synthetic size profiles, not a multi-scene
or cross-hardware quality corpus.

## Tests, build and viewer

321 frontend tests pass (310 baseline +11), typecheck and targeted lint pass.
New contracts cover eleven equal shapes, changes to each of the four dimensions,
range-local cache lifetime, alternating shapes, out-of-bounds smaller streams,
missing source/destination, rejected GPU copies and the exact stream/rectangle
sequence for thirteen diagnostic streams. The first red run failed because the
new module did not yet exist; it is retained, not represented as a behavioral
regression test failure against the old method.

BIGZEN production Docker build passes. Existing optional-peer warnings remain;
the prune audit reports zero vulnerabilities. Native/CUDA code and dependencies
are unchanged; native suites are deliberately not rerun for this frontend change.

The production candidate loads Saint-Etienne, handles a Shift-drag pan, and
reloads to the initial view. Bundle identity remains
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Initial and reloaded cuts select exactly the same 374 node IDs / 7,461,366 splats
as the prior version, with one merged resource/entity and equal world-active count.
The pan settles at 370 nodes / 7,494,204 splats; its last update adds 335,380 and
reuses 7,158,824 splats. All captured settled states have zero pending nodes,
decoder fallbacks and assembly-worker failures.

Observed LOD/commit times: initial 8,192.6/370.0 ms, pan 513.2/38.1 ms, reload
3,119.1/348.2 ms. These have different cache/viewport histories and are functional
checks, **not controlled before/after speed comparisons**. Captures are retained;
the facade is visible after the pan. Full-page screenshot capture timed out;
viewport capture succeeded. No quantified PLY image comparison, human acceptance
of this exact revision, frozen door→facade replay or browser matrix is claimed.

## Evidence, deployment and rollback

Retained in WSL and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-copy-plan-20260827`.
Includes reference/candidate source archives, executable CPU/GPU pages, all raw
JSON, module provenance, independent validation, tests/build logs, viewer states,
screenshots and SHA-256 manifest. No older experiment, image or container is deleted.
The README supplies exact reproduction URLs and the `node validate.mjs` command.

Candidate helper source SHA-256:
`7119d95203824ed99643d8ef8a5b4c3de8bbc6bfaffb612e6a7086eb62305af0`.
Frontend archive SHA-256:
`0ea7704429d690df782e049b05fdc033eb39dde4189d03df1251ecd0d3d197de`.
Image `droneai-frontend:69e848c`:
`sha256:6ee85b552dda814cdb6420be30dfa4b44af1daf14ca0377d8fce692309c6f91d`.
Container `droneai-frontend-gstile-69e848c`, loopback port 3000.

Previous container `droneai-frontend-gstile-7d7dd41` is retained stopped, image
`sha256:a5b8a4d0c44cf15bb328b42ff392797408c1875e3cff0fa6e7266ee6198a192e`.
The deployment script checks identities and restores the old container on launch
or health failure. For manual rollback, verify identities, stop the candidate and
start the retained previous container. That fallback is available but was not
fault-injected here. The first startup health request raced server readiness;
bounded retry succeeded. Fixture server and bundle are unchanged.
