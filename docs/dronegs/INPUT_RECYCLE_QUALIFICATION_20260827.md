# GSTile — bounded decoder-input recycling, 2026-08-27

## Decision and scope

Reference: `81da85d77d79912e37381e0b89f6fa356b0c46d2` (PR #270).
Implementation: `7d7dd4141957749a9823e0ac1ee2af0e0a6e5ef2`.

Retain the existing defensive copy from the SHA-verified range cache, but reuse
the destination buffer when a decoder Worker returns it. This removes repeated
input-buffer allocations on the warm path; it does **not** remove the 96 B/splat
copy, move that copy off the main thread, reduce network bytes, or change rendering.
No shader, arithmetic, SH, opacity, sort, culling, cut, budget, bundle or tiler
change. The merged renderer and previous off-thread assembly policy are unchanged.

One Worker slot owns at most one recyclable scratch buffer. Its next input is
copied into the active prefix using `Uint8Array.set`; the Worker decodes a bounded
byte view with exactly `recordCount * 96` bytes. A larger scratch buffer's stale
tail is neither decoded nor published. The native decoder's exact shape check
still applies to the view. No extra slice is introduced inside the Worker.
The original cached pack is never transferred, mutated or detached.

On success the Worker transfers the scratch buffer back alongside the twelve
native output buffers. The pool validates its exact expected capacity and rejects
aliasing with the cache or output streams. It does not expose the scratch buffer
in the public decode result. Missing, malformed, wrong-sized or aliased recycled
input fails the slot; the existing backend disables its pool and takes the
synchronous native fallback. Corrupt tiles are not silently accepted.

Queued cancellation still removes its task without allocation. An already-running
cancelled decode retains its slot until its response; only then can a queued task
reuse its scratch buffer. Worker failure/replacement and pool disposal drop spare
references. No extra Worker, shared memory, security header or dependency is added.
This follows the standard [ArrayBuffer ownership-transfer contract](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects).

## Memory, telemetry and comparison switch

Recycling is capped at **8 MiB per Worker**: at most 32 MiB for the default four
Workers, or 64 MiB for the supported explicit eight-Worker pool. A larger tile
uses an exact fresh slice and is not retained. Smaller tiles can reuse a larger
buffer; buffers grow only as required within the cap. They are released with the
backend, not retained in a cross-viewer cache.

The previous assembly counter remains a bound on logical active input/output
payload (128 MiB), not on retained scratch capacity. Recycled capacity includes
idle buffers and unused tails. A conservative combined bound is therefore
**128 + 32 MiB** for that path with the default pool, excluding destination
columns, network cache, output staging, GPU memory, browser internals and GC.
This is an ownership/accounting bound, **not a measured process RSS/VRAM peak**.
The measured retained high-water size in the large component runs is 24 MiB.

Existing `inputCopyMs/Bytes` retain the explicit copy duration and active byte
count, whether sliced or copied into a returned buffer. Added fields:

| Field | Meaning |
|---|---|
| `inputAllocatedBytes` | Sum of newly allocated defensive input bytes. Includes synchronous fallback slices. |
| `inputReusedBytes` | Sum of active input bytes copied into existing scratch buffers. Not bytes avoided on the network or copies eliminated. |
| `performance.decodeInputRetainedBytes` | Current idle scratch-buffer capacity owned by the pool, not in-flight or peak memory. |

The first two fields sum to `inputCopyBytes` for the recorded completed work;
failed/cancelled Worker tasks are not included, consistent with existing counters.
Durations are task sums, not additive wall times. The developer HUD shows recycled
input MiB. `gstileRecycleInput=0` disables only this recycling for same-build A/B
and rollback; `gstileWorkerAssembly=0` remains a separate control.

## Research and rejected variants

A five-arm exploratory screen used `ArrayBuffer.slice`, the typed-array copy
constructor, `Uint8Array.slice`, fresh allocation plus `.set`, and returned-buffer
reuse. Ten alternating-order blocks, two warmups per arm, 72 tile tasks per sample,
four real echo Workers; every payload hash was checked. Ranges start at byte 127
inside guarded source buffers. The wall counter includes Worker startup and hash
work, so only the explicit main-thread copy counter is used to screen candidates.
That first reuse screen has an exact-size buffer map bounded by these three
fixtures; it is not the final one-buffer-per-slot implementation.

| Variant | Median copy ms | Median paired change vs slice | Decision |
|---|---:|---:|---|
| Existing buffer slice | 82.25 | reference | Retain for fresh/oversized inputs |
| Typed-array copy constructor | 80.30 | -1.28% | No compelling improvement |
| Typed-array slice | 82.75 | +2.53% | Reject |
| Fresh allocation + set | 79.60 | -3.52% | No compelling improvement |
| Returned-buffer reuse | 63.65 | -22.77% | Qualify in the real decode pipeline |

The [V8 slice implementation](https://chromium.googlesource.com/v8/v8/+/refs/heads/main/src/builtins/builtins-arraybuffer.cc)
and [typed-array copy implementation](https://chromium.googlesource.com/v8/v8/+/25f0e32915930df1d53722b91177b1dee5202499/src/builtins/typed-array-slice.tq)
helped choose the candidates. These upstream sources are not proof of the exact
Chrome binary's allocation behavior; measured results determine the decision.
No uninitialized-memory or browser-specific API is used in application code.

## Integrated component protocol and results

Windows Chrome 151.0.0.0, hardwareConcurrency 20. Same immutable Saint-Etienne
bundle `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Three original SHA-verified Q96 fixtures: `r` (16,384), `r01001001000` (32,687),
`r1100100010000` (65,536). Full pack/payload identities and quantization retained.
They are repeated to make controlled sizes, not treated as independent scan data.

Reference uses the exact compiled PR #270 decoder pool. Candidate uses the exact
compiled 7d7dd41 pool/Worker/native decoder, with source/module SHA provenance
validated against that commit. Both have four real decoder Workers and the same
merged-copy implementation. Large runs use the prior assembly Worker, small runs
copy into main-thread columns. Four task lanes await consumption before continuing;
this is a controlled component scheduler, not the production network scheduler.

Every measured arm creates a fresh pool: initial allocations, Worker startup,
input copies, decode, admission, output assembly and handoff are timed. Thus the
benefit is from reuse **within** the run, not a prefilled candidate-only pool.
Fixture fetch and SHA/oracle validation, GPU upload and rendering are excluded.
Two warmup passes per arm, alternating AB/BA pairs, fresh page between series,
no forced GC or outlier removal. All twelve complete output-buffer SHA-256 hashes,
including padding, and bounds match on every sample. Cached input hashes still
match at the end. A separate validator checks the sample order and byte counters.

| Records / series | Pairs | Copy median baseline / candidate ms | Paired copy change | Wall median baseline / candidate ms | Paired wall change |
|---|---:|---:|---:|---:|---:|
| 3,208,996 / 1 | 10 | 82.90 / 63.50 | -23.70% | 825.80 / 791.35 | -5.20% |
| 3,208,996 / 2 | 10 | 82.80 / 61.95 | -23.57% | 802.95 / 746.15 | -6.68% |
| 7,449,455 | 6 | 187.40 / 139.70 | -25.67% | 1,761.15 / 1,503.35 | -16.30% |
| 343,821 / main assembly | 12 | 10.85 / 9.65 | -5.37% | 187.65 / 190.40 | +1.38% |

These are medians of paired ratios, not ratios of the table's medians. Input
allocation-volume reductions are 87.75%, 87.75%, 95.16% and 14.27% respectively.
Memory is still copied; this metric is not a reduction in total application memory.
The small cold case is essentially unchanged/slightly slower, not a demonstrated
small-cut speedup. Large-case results support enabling the bounded reuse path.

Median maximum 10 ms timer gaps, baseline/candidate: 56.95/54.40, 57.20/55.70,
22.40/21.15 and 22.45/23.00 ms. This is not a frame-time or input-latency benchmark.
GC and system activity are uncontrolled, the series are from one machine, and
wall-time improvement is not attributed wholly to the explicit copy timer. No
general FPS, network-load, PLY-image metric or cross-browser performance claim.

Two earlier production-decoder prototypes (8 pairs at 3.21M, 6 at 7.45M) had
paired copy gains of 27.10%/28.65% and wall gains of 10.05%/14.56%. They used a
minimal four-caller pool, not the final failure-handling implementation. Keep
them separate from the integrated results; all sources and raw outputs remain.

## Automated and operational qualification

310 frontend tests pass (294 baseline +16), plus typecheck, targeted lint and
repository static gates. Tests cover bounded byte views and exact output,
grow/shrink reuse, the 8 MiB cutoff, malformed/aliased returned buffers, explicit
disable, active cancellation followed by queued reuse, per-slot ownership and
disposal. The initial red run is retained, including teardown rejection noise
caused by early assertions against the old implementation.

Real Chrome drills pass for large-to-small reuse, previous-output isolation,
active abort followed by queued recovery, invalid returned capacity and an
uncaught Worker crash with queued recovery. All output hashes match a fresh native
decode, all cached inputs remain intact: five Workers created, five terminated.
These exercise actual message/transfer boundaries. Automatic backend fallback
was not fault-injected in the production viewer. The existing decoder-pool policy
for a silent Worker is unchanged; this lot does not introduce or qualify a timeout.

The BIGZEN production Docker build passes. Existing optional-peer warnings remain;
the prune audit reports zero vulnerabilities. No native/CUDA source or dependency
changed; no native/GPU numerical kernel build is needed for this JavaScript lot.

## Production viewer

The same production image was exercised with recycling disabled, then enabled,
and that sequence was repeated on warmer data. All initial snapshots select
the same 374 node IDs / 7,461,366 splats as PR #270, with one enabled merged
entity/resource and matching world-active count. Zero pending work, decoder
fallbacks or assembly failures at settled snapshots. Each last large update
adds 7,265,600 splats, hence 697,497,600 active input bytes and 1,249,683,200
output-copy bytes in the unchanged assembly Worker.

| Observation | Input copy ms | Fresh input bytes | Reused input bytes | LOD wall ms |
|---|---:|---:|---:|---:|
| Same-build disabled, initial | 304.1 | 697,497,600 | 0 | 7,018.5 |
| Default, first | 191.0 | 73,505,760 | 623,991,840 | 2,995.0 |
| Same-build disabled, warmer | 286.4 | 697,497,600 | 0 | 3,012.3 |
| Default, repeated | 178.2 | 77,260,032 | 620,237,568 | 2,587.3 |

Do not attribute the initial 7.0 -> 3.0 seconds to recycling: the first disabled
run has 145 persistent-cache hits and 409 network responses, compared with 404
persistent hits / 126 network responses in the next default run. These page-load
observations have different cache/prefetch/GC histories, not controlled latency
pairs. The later on/off snapshots corroborate buffer reuse and exact cut identity;
the component experiment above is the controlled evidence for timing claims.

A short pan completes at 369 nodes / 7,477,820 splats. Its last update adds
1,288,047 splats (<2M), uses the main-thread assembly as intended and copies all
123,652,512 input bytes into recycled buffers, with **zero fresh input allocation**.
Input copy is 35.0 ms, output copy 94.1 ms, LOD wall 618.3 ms. Idle scratch capacity
is 24,324,288 bytes, below the 32 MiB cap. This single gesture is a functional
check, not a repeatable door-to-facade performance benchmark or proof that every
production in-flight cancellation is covered.

The viewer was restored to the default initial view; viewport screenshot retained.
Shader/quality settings are unchanged and native streams are bit-identical in the
component oracle. Quantified image comparison against the original PLY and the
user's visual acceptance of this exact revision remain separate/unperformed here.

## Evidence and rollback

All evidence retained in WSL and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-input-copy-20260827`.
Includes source archives, fixtures, the rejected copy variants, both prototypes,
compiled-module provenance, all raw samples, tests/drills, build/deploy logs and
viewer captures. No previous artifacts, images or containers were deleted.

Candidate source archive SHA-256:
`928f5ab254c9c5b874e05ee1631f7774565761d6ade9de733c6a5439034431cd`.
Image `droneai-frontend:7d7dd41`:
`sha256:a5b8a4d0c44cf15bb328b42ff392797408c1875e3cff0fa6e7266ee6198a192e`.
Container `droneai-frontend-gstile-7d7dd41`, loopback port 3000.
Previous container `droneai-frontend-gstile-c352fa2`, image
`sha256:d166370c4351e809947eedc186f88c1cfc3af2a7a46ee3f1e2729aed7801d1d1`,
is retained stopped. For rollback verify identities, stop the candidate and start
that container, or use `gstileRecycleInput=0` for a same-build comparison.
The first health request raced startup; bounded retry succeeded (Next ready in
176 ms). Fixture service/bundle and native DroneGS dev.72 remain unchanged.
