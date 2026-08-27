# GSTile arena CPU centers — exact bulk copy, 2026-08-27

## Decision and scope

Ship a correctness fix and a bounded copy optimization. The incremental merged
arena copied CPU sort centers from `columns.position`, but merged decode uses
`centerBounds: true`: those three PLY columns are empty and the real positions
are in `centerStream`. Reading the empty arrays wrote `undefined` as Float32
`NaN`. A red test reproduces this on the extracted original loop.

The new helper reads the authoritative interleaved stream with
`Float32Array.set(subarray(...))`, preserving offsets and exact bits. It keeps
the legacy unpacked-column fallback and validates ranges/source lengths before
writing. PlayCanvas 2.21.4's unified CPU sorter consumes `resource.centers` in
`_feedCpuSorterCenters`; the default GPU renderer consumes the unchanged GPU
streams. This is a CPU-center invariant violation, not evidence that previously
approved GPU screenshots were wrong.

Implementation: `f4b516b0307e4c92ca02174249e5ed144eb03f1f`.
Reference: `0d8b2439ab9ee164ef7d0cf1ab315b0d981939b2` (PR #268).
Only the copy helper, its tests and the backend call change. No shader, Q96,
texture content, LOD rule, GPU budget, sorting mode, network cache or Worker
ownership change. Merged stays the default. Native DroneGS stays dev.72.

## Resource and compatibility contract

Production source/destination buffers are distinct and already allocated.
Each span copies 12 logical bytes per splat; `subarray` creates only a view,
not a second full data buffer. No new cache, Worker, texture, queue, SAB or
cross-origin isolation requirement. Source buffers are neither mutated nor
detached. The generic packed helper also supports overlapping ranges.

Same-type typed-array copying preserves the bit encoding, as specified by
[ECMAScript SetTypedArrayFromTypedArray](https://tc39.es/ecma262/multipage/indexed-collections.html#sec-settypedarrayfromtypedarray).
Tests explicitly cover signed zero, a NaN payload, subnormal values and infinity.
This is not a finite-value filter: source bits remain authoritative.

This lot does **not** remove the much larger 96 B/splat input and 172 B/splat
Worker-result-to-merged-column copies. Nor does it remove the CPU center array
needed by the diagnostic CPU sorter. It optimizes the subsequent center copy
during arena commit, not Worker output assembly.

## Reproducible component benchmark

The archived harness runs on the **main thread** in Windows Chrome 151,
hardwareConcurrency 20. It decodes the same three SHA-verified Saint-Etienne
Q96 fixtures used in [the preceding qualification](NATIVE_DECODE_EXP_REUSE_20260827.md):
`r` (16,384), `r01001001000` (32,687), `r1100100010000` (65,536).
Bundle: `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
The 114,607 exact decoded centers are repeated to form controlled copy sizes;
the 3M/7.5M cases are synthetic span schedules, not captured camera trajectories.

Comparator: a **correct scalar interleaved loop**, not the old NaN-producing
loop. Therefore the reported speedup is against a straightforward correct fix,
not a claim of end-to-end improvement over the previously deployed build.
Candidate is the exact TypeScript transpilation of the committed helper;
`module-provenance.json` and `validate-evidence.mjs` verify source/output hashes.

Before timing, compare every destination Uint32 bit plus an independent source
oracle, including nonzero source/destination offsets, gaps and tail sentinels.
Hash the full output. Each page reload performs 12 warmups and 24 alternating
AB/BA pairs per scenario. Three page reloads; 72 pairs per scenario. The 65,536
case repeats each timed batch 32 times for timer resolution and reports time
per copy; other cases use one copy. No forced GC or outlier removal. Initialization,
fixture decode, SHA checks, comparison, network, GPU uploads and render are
outside timing. Span iteration, validation and subarray views are inside.

| Records / spans | Run | Scalar median ms | Bulk median ms | Paired reduction |
|---|---:|---:|---:|---:|
| 65,536 / 1 | 1 | 0.291 | 0.036 | 85.91% |
| 65,536 / 1 | 2 | 0.294 | 0.041 | 85.57% |
| 65,536 / 1 | 3 | 0.298 | 0.053 | 81.30% |
| 3,000,000 / 92 | 1 | 13.15 | 8.15 | 39.40% |
| 3,000,000 / 92 | 2 | 11.60 | 6.30 | 44.37% |
| 3,000,000 / 92 | 3 | 10.85 | 7.60 | 41.70% |
| 7,500,000 / 229 | 1 | 36.00 | 22.60 | 38.67% |
| 7,500,000 / 229 | 2 | 33.50 | 20.40 | 41.37% |
| 7,500,000 / 229 | 3 | 33.65 | 20.45 | 42.04% |

Pooled paired reductions: 83.65%, **41.17%**, 40.50%, respectively. These are
component medians, not FPS, first-load latency, p95/p99 or confidence intervals.
All output hashes agree across the three runs. Background system activity and
browser GC are not controlled; repeated pairs are not independent devices.
Firefox/Safari performance and peak process RSS/VRAM were not measured.

Harness live source plus two destination arrays: 2,360,220 / 108,038,052 /
270,093,948 bytes per scenario, plus the small fixture seed, decoder allocations
and runtime overhead. These are explicit array sizes, **not measured peak RSS**;
GC may retain earlier allocations. Production adds no corresponding arrays.

## Automated and operational qualification

- 270 frontend tests pass (20 new center-copy cases); typecheck and targeted
  lint pass. Repository static gates pass.
- Tests cover packed/legacy sources, exact Float32 bits, overlapping packed
  ranges in both directions, fragmented spans, untouched holes, invalid offsets,
  truncated inputs, and empty ranges. The original packed-source test fails
  with six NaNs, then passes after the fix.
- The initial attempt to run all red tests on the unguarded old loop reached
  the enormous invalid-count case and was terminated by exact test PIDs.
  `red-centers.log` and the successful targeted reproduction
  `red-packed-centers.log` are both retained. This was a test-harness incident,
  not a production hang observed in navigation.
- BIGZEN production Docker build passes with locked dependencies. Existing
  optional npm peer warnings remain; npm prune reports zero vulnerabilities.
- Chrome default GPU mode: baseline and candidate select exactly the same 374
  node IDs and 7,461,366 splats after initial load. One entity/resource/placement,
  pending nodes zero, Worker fallbacks zero.
- After camera navigation the candidate reaches a different stable cut:
  369 nodes, 7,477,820 splats; the last update adds 285,740, reuses 7,192,080
  and removes 295,522. This exercises incremental commit. Its 33.3 ms commit
  is an observation, not an A/B performance result.
- One automated scroll command timed out; the subsequent DOM and screenshot
  confirmed the changed, completed cut. The command is not counted as a
  reproducible gesture. Viewport PNG and raw snapshots are retained.
- Explicit diagnostic `gstileSort=cpu` also loads and completes an incremental
  camera-pan cut: 369 nodes / 7,477,820 splats, 285,740 additions, 7,192,080 reused,
  one merged resource/entity, zero pending nodes/fallbacks. A screenshot confirms
  scene rendering. This is a CPU-path smoke check, not GPU/CPU image equivalence
  or a CPU-mode speed claim. The final page is restored to default GPU sorting.

Initial full-load times differ with cache state (6.54 s baseline / 3.28 s
candidate); **do not attribute that difference to this patch**. No quantified
PLY image comparison or user visual acceptance of this exact source is claimed.

## Evidence, deployment and next work

Evidence retained locally and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-center-copy-20260827`.
Contains all tests, raw Chrome samples, fixtures, transpiled modules, provenance,
standalone benchmark UI, source archive, scripts, build/deploy logs and viewer
snapshots. No failed trial or previous qualification was deleted.

Archive SHA-256:
`8313ac347d915bf9290af9442b0924686fc6431d031620a9fa1b0246ce628ed3`.
Image `droneai-frontend:f4b516b`:
`sha256:3dd537a93c4e7fc6e5a9ef182e9c1d8d71336f10fc6d5037c5152b02abd96c49`.
Container `droneai-frontend-gstile-f4b516b`, loopback port 3000,
restart unless stopped. Existing fixture server and bundle unchanged.
Rollback: verify image IDs, stop the candidate and start retained container
`droneai-frontend-gstile-a7f0cd0` (image
`sha256:2ce63ad22007146523143aa356279887bb5aeb249cc0b716e358f4d0d854da5a`).
No source or bundle deletion is required.

The higher-potential next experiment remains Worker-output assembly off the
main thread or bounded native-stream adoption. It needs explicit backpressure,
cancellation/ownership tests and peak memory measurements before promotion.
Shared cache inputs must not be detached merely to eliminate `slice()`.
