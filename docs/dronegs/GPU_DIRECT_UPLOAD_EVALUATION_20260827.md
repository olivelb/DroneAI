# GSTile — direct GPU upload evaluation, 2026-08-27

## Decision

**Do not promote direct `queue.writeTexture` arena updates in this form.**
Four isolated variants preserve all eleven GPU streams, but their synchronous
host phase is slower than staging. On an already-initialized arena, coalesced
direct writes increase paired median host time by **71.66%**, or **60.84%** with
explicit per-stream submission. Paired median absolute increases are 45.70 and
37.75 ms respectively. This fails the objective of limiting main-thread pauses,
despite shorter time to GPU queue completion in this component experiment.

This is a negative result for these prototypes, **not proof that every direct
upload or GPU-side scatter implementation is ineffective**. No renderer, tiler,
shader, cut, cache, dependency or default is changed. No performance improvement
is deployed by this report. The previously qualified merged renderer remains.

Baseline: `eef1a4f8bba7629016c5efa1350dd956e9416cea`, clean authoritative WSL checkout
at experiment start. The source archive, executable prototypes, independent
validator and raw results are retained outside Git. This documentation-only
delivery follows the project's requirement to measure before promotion.

## Hypothesis and variants

The existing incremental merged path constructs temporary packed textures,
uploads them, then copies their linear ranges into the persistent GPU arena.
`planLinearTextureCopies` splits at both source and destination row boundaries.
Bypassing staging might remove intermediate GPU storage and texture copies.

The direct prototype writes exact bounded typed-array subviews to each destination
range: partial first row, a full-width rectangle of complete rows, partial last
row. The coalesced variant first joins ranges only when **both** source and
destination are contiguous; it does not write through holes occupied by residents.
No arithmetic, packing, quantization or stream precision changes.

The baseline uses the exact compiled planner from the reference source, with
eleven uploads and per-stream command-buffer submission. The last direct variant
also explicitly calls `queue.submit([])` once per stream to test eager submission
without yielding JavaScript. Counters include writes/copies, not submissions.
The native PlayCanvas adapter itself is **not** replaced or bypassed in the app.

Sources: installed PlayCanvas 2.21.4 `WebgpuTexture.uploadTypedArrayData`,
[writeTexture contract](https://developer.mozilla.org/en-US/docs/Web/API/GPUQueue/writeTexture),
and [Chromium queue implementation](https://chromium.googlesource.com/chromium/src/third_party/+/e16eac7ad9cc1761305bdad6c4882392bde677ff/blink/renderer/modules/webgpu/gpu_queue.cc).
The cited Chromium implementation bounds the copied byte span and eagerly flushes
explicit submission. It informed the experiment, but does not identify the cause
of the stall or establish the implementation of the installed Chrome binary.

## Frozen component protocol

- Windows Chrome 151.0.0.0; adapter reports `nvidia` / `lovelace`.
- Workstation: i9-13900H, NVIDIA RTX 4070 Laptop; Windows driver 32.0.16.1062.
  Intel UHD is also enumerated, but is not the adapter reported by the experiment.
- WSL Linux 5.15.167.4, Node 24.14.0; installed frontend PlayCanvas 2.21.4.
- Synthetic **1,288,047 splats**; source textures 1,135 × 1,135; destination
  arena 2,739 × 2,739. Counts/dimensions match a prior Saint-Etienne pan, but
  **data and slot ranges are synthetic**, not a replay of that gesture or PLY.
- Fifty-two source-contiguous ranges, destination offset 17, 13-splat gap after
  every eight ranges. Coalescing leaves seven ranges. All exact ranges are in JSON.
- Eleven streams: two RGBA16F, five RGBA32UI, four RGBA32F; 160 bytes per splat.
  Deterministic finite float/half bit patterns and integer patterns are retained.
- Two warmups per arm, six alternating AB/BA pairs per variant, 100 ms between
  arms; no forced GC, no outlier removal, no other viewer opened during timing.
  OS activity, clocks and garbage collection are not controlled.

The first two experiments create new destination textures per sample. Allocation
calls are outside timing, but lazy initialization need not be. The latter two
zero-upload and fence the persistent destination **before all samples**, then
reuse it. These are distinct profiles: do not combine their absolute timings.
Reuse also repeats the same payload; the fresh-destination checks remain important
because an omitted write could leave a previously correct value in resident tests.

`submitMs` measures the synchronous host phase, including baseline staging
allocation, planning, writes/copies and explicit submissions. `completeMs` extends
through `queue.onSubmittedWorkDone()`. Neither is a GPU timestamp. Readback and
hash checking are outside both timers. No rendering, sort, world reset, PlayCanvas
resource/entity construction or production retirement policy is in this harness.
Raw textures use COPY_SRC, COPY_DST and TEXTURE_BINDING, without PlayCanvas's
additional RENDER_ATTACHMENT usage. This is another integration difference;
the measured ratios must not be applied directly to production frame timings.

## Results

Each row is its own six-pair experiment. Times are medians in milliseconds;
percentage changes are **medians of paired ratios**, not ratios of those medians.

| Variant / destination | Host staging / direct ms | Paired host change | Queue-complete staging / direct ms | Paired queue-complete change |
|---|---:|---:|---:|---:|
| Per-range direct / fresh | 64.15 / 503.05 | +690.38% | 936.65 / 894.45 | -6.19% |
| Coalesced direct / fresh | 60.60 / 517.00 | +715.98% | 944.40 / 879.80 | -8.23% |
| Coalesced direct / resident | 63.25 / 103.40 | +71.66% | 231.00 / 146.75 | -39.07% |
| Coalesced, submit per stream / resident | 63.50 / 96.75 | +60.84% | 225.15 / 154.40 | -31.76% |

Baseline: 18,216 texture-to-texture copies plus 11 writes per arm. Per-range
direct: 1,716 writes; coalesced direct: 231 writes. All direct variants upload
206,087,520 logical bytes and use no explicit staging texture. Baseline uploads
206,116,000 bytes including padding and copies 206,087,520 bytes on the GPU.
The arena itself is 1,200,339,360 logical texture bytes in both arms.
These are allocation/transfer formulas, **not measured process RSS/VRAM peaks**;
browser/driver internals may still stage direct writes.

Coalescence and eager submission do not remove the host regression in this
profile. The completion-time improvement is worth preserving as a future lead,
but cannot be presented as improved interaction latency or product FPS. The
much larger fresh-arena costs also show why an allocation-only measurement must
not be used as an estimate of the already-resident update path.

## Correctness and limitations

Every sample and warmup reads back all eleven full destination textures with
256-byte-aligned row pitches, removes padding and checks SHA-256 against a CPU
scatter oracle, including holes and untouched arena tail. Validation errors fail
the run. The independent Node validator reconstructs the source patterns and
expected destination hashes, verifies all **704 texture hashes** across 64 arms
(48 measured, 16 warmup), range bounds, call/byte counters, order and summaries.
All pass. These are repeated checks of one deterministic fixture, not 704
independent datasets. Source arrays are not mutated by the harness; no browser
before/after source-hash measurement was collected.

No PLY image comparison, actual door→facade replay, frame-time/input-latency
distribution, 7.5M update, memory peak, browser compatibility matrix or other-GPU
qualification was run. No integrated renderer path was implemented, so there is
no operational fallback/device-loss or partial-write recovery qualification.
No new full frontend/native build or test suite is required for this docs-only
decision; the independent component validation and documentation gates are the
checks for this delivery. The existing viewer endpoint still answers HTTP 200.

Before revisiting direct upload, first capture actual arena ranges and measure
host pauses alongside the full render workload. Reuse of row-copy plans across
streams was subsequently implemented and qualified in
[the shared-plan follow-up](SHARED_COPY_PLAN_QUALIFICATION_20260827.md): a small
CPU saving, with unchanged GPU copy volume. GPU scatter with fewer CPU API calls
remains **unmeasured and not enabled**, adding shader/storage-usage and lifecycle
risks. It must preserve exact coefficients, resident gaps, cut identity and bounded
memory, and pass target-GPU integration and visual acceptance before promotion.
Do not lower quality to compensate for transport or scheduling costs.

## Reproduction and retained evidence

Directory in WSL and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-direct-upload-20260827`.
Contains baseline source archive, planner/module provenance, PlayCanvas source
and license, four separate prototype sources/pages, raw JSON, independent
validator, summary, runtime record, gate logs and SHA-256 manifest. Nothing from
this or preceding lots is deleted; no application image/container is replaced.

The retained README provides the exact four page URLs and protocol. Serve the
directory on loopback port 3015, open one page at a time in Chrome, wait for
`Terminé`, and save `#result` before navigating. Then validate:

```sh
node validate.mjs prototype-1m.json prototype-coalesced-1m.json \
  prototype-resident-1m.json prototype-flushed-1m.json
```

Reference planner source SHA-256:
`82c61f26b8d0ccab9ee36f658ca3812ddb9c7a25c8e5c88191ca7fdc2c853dcb`.
Compiled planner SHA-256:
`dd4446e595d70772e46965f9e957a767367c3eb89c50f5bb1f8111f560b9a1ea`.

Raw result SHA-256, in table order:

```text
bdc5a0aa6132d4ac2d70225e6041607d980b9c4e552aa550f7bccbe4b0a0ae78
915003fc69bbce27c74b19ad12cfa2fe689ca918e2285005b4e7f5eb6c877e22
e07f60aa3dd56261d88e74690387cd2c047fddec902cdacba6172e6a214720a6
f280061866d67089ed197e9152888507b3d343ab01fcac0e57129f0cfb470f16
```
