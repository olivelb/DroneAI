# GSTile native decode — exact exponential reuse, 2026-08-27

## Verdict

Promote one small decoder optimization: reuse the three already-computed
scale exponentials when calculating the conservative bounds. Chrome Worker
comparison on three immutable Saint-Etienne tiles shows **6.49% lower paired
median decode time**, with identical output bytes and bounds. This is a
component result, **not a measured FPS or full-load improvement**.

Do not promote either SH experiment. Their paired Node medians regressed;
both patches, compiled modules and measurements are retained, not shipped.

Qualified implementation: `a7f0cd06168c379964b81f7e452d5c53409f0783`.
Reference: `cf15ff153bb9980dee04b36b3dab4bb1d10f1012` (PR #267).
Only `native-decode.ts` and its tests change in the implementation commit.

## Exactness and resource contract

Previously each record computed `exp(sx)`, `exp(sy)`, `exp(sz)` for transforms,
then `exp(max(sx, sy, sz))` again for bounds. The candidate retains the three
unrounded JavaScript results and selects the one whose **original log-scale
argument** equals that maximum. This avoids relying on monotonic approximation
of `exp` or using the half-float transform value. NaN propagation is retained.
The sigmoid still uses its existing exponential: four calls per valid record
instead of five, with the same scalar formulas and inputs.

No extra arrays, caches, messages or GPU resources. Input/output buffer sizes,
ownership, padding, Q96 decoding, SH packing, opacity, Worker scheduling,
fallbacks, LOD and GPU budget are unchanged. Merged remains the default.
The main-thread copies measured in the previous lot are **not removed** here.

## Protocol and provenance

Dataset bundle:
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Read-only source on BIGZEN:
`/mnt/i/DroneAI-GSTile-Tests/saint-etienne-facade-1mm-v2-no-opacity-sh-no-rgb-adaptive-v4-pack2m/bundle`.

| Fixture | Node | Records | Payload SHA-256 |
|---|---|---:|---|
| Proxy | `r` | 16,384 | `51a1788e9d8399b3dce1e8b0e1f38320df5d03155f2753fce5b6f2e1df8a794d` |
| Leaf | `r01001001000` | 32,687 | `b54f7a429e95bf1eca5d6971164c3fa441c4112912bff1d4767dd64bda024b75` |
| Large leaf | `r1100100010000` | 65,536 | `59e49be3c35b31a0ddd3bba4d5076cb3d7e2c90c0d153cd2c28cd36430def4ea` |

Choose the largest proxy <=65,536, leaf <=32,768 and leaf in (32,768,65,536],
breaking ties by node ID. Full source packs are SHA-verified before extracting
payloads; full packs and manifest are retained. Each timed call decodes all
114,607 records in the same fixture order. These real tiles have colour SH3
and opacity SH0; the expanded synthetic oracle additionally covers nonzero
opacity-SH coefficients.

The archived baseline and each isolated candidate are transpiled with the
repository's locked TypeScript to ES2022 modules (no code minification).
`validate-evidence.mjs` verifies that the benchmark candidate module is exactly
the transpilation of the committed decoder. Production is separately built
with the unchanged Next.js/Turbopack Docker configuration and smoke-tested.
This is not a direct timing of the minified production chunk.

Before timing: compare all 12 typed-array buffers byte-for-byte, including
texture padding, plus bounds; record SHA-256 of every output stream in Chrome.
Use native `Float16Array`, then 12 warm-up iterations for both variants and
24 alternating AB/BA pairs. Allocation is included; network, input copying,
SHA verification, output comparison, transfers, GPU upload and rendering are
outside the timed region. Consume output in a checksum sink. No manual GC.

Node screening: Node 24.14.0, local Ubuntu WSL. Target confirmation: a dedicated
Worker in the user's Windows Chrome 151.0.0.0, reported hardwareConcurrency 20.
Three runs each create a fresh Worker; their timed samples are warmed, **not
cold first-call measurements**. Other machine activity is not isolated.

## Results and rejected variants

Each Node screening run uses its own interleaved reference, not the reference
timing from another run. The paired ratio is median(candidate/baseline).

| Node candidate | Reference median ms | Candidate median ms | Paired change | Decision |
|---|---:|---:|---:|---|
| Fuse maximum-SH reduction into dequantization | 40.70 | 41.72 | +3.96% | Reject at screening |
| Unroll SH writes to fixed offsets | 41.18 | 47.62 | +17.54% | Reject at screening |
| Reuse scale exponentials | 45.94 | 38.64 | -10.08% | Proceed to Chrome |

The SH results do not establish inefficiency in every browser. They show no
reason to promote the extra code on this measured configuration. Fewer source
loops are not automatically faster optimized JavaScript. DataView was left
unchanged: V8's [DataView implementation report](https://v8.dev/blog/dataview)
is useful background, not evidence of a speedup on today's runtime.

| Chrome run | Reference median ms | Candidate median ms | Paired reduction |
|---|---:|---:|---:|
| 1 | 58.70 | 55.45 | 6.94% |
| 2 | 58.20 | 55.35 | 6.49% |
| 3 | 59.45 | 54.80 | 6.64% |

All three independent Worker runs agree in direction. Pooled median of the
72 pair ratios is 0.9351088 (-6.49%). These are 72 pairs within three sessions,
not 72 independent devices or a confidence interval. Raw samples, outliers,
fixture metadata and stream hashes remain in the JSON reports.

## Correctness and operational checks

- Red test: original decoder calls `Math.exp` five times, expected four after
  the change. Green candidate passes that work-count contract.
- Bounds tests cover each maximum axis, ties, very small scales that round to
  zero in half precision and large scales, on both native and fallback paths.
- The 16,384-record exact decoder/packer oracle now also randomizes DC, SH,
  base opacity and all opacity-SH coefficients; it formerly populated only
  transform bytes. It passes exact arrays and bounds.
- Full frontend: **250 tests / 35 files pass**. Typecheck, targeted ESLint,
  `make static`, production build and browser benchmark equality pass. The
  initial test tuple annotation error was corrected; its log is retained.
- Production viewer smoke: same 374 initial node IDs and 7,461,366 target
  splats as the prior build, zero pending nodes, zero Worker fallbacks, and a
  rendered viewport screenshot. The retained validator checks the same cut.

The production smoke's last cut took 5.65 s, with 173 persistent-cache hits
and 669.5 MB cumulative network bytes (304.4 MB prefetch). The preceding lot's
reload used a different cache mix. Neither this number nor a comparison to
that earlier 3.06 s snapshot measures a causal regression or acceleration.
The isolated decoder result must not be relabelled an end-to-end gain.

Not qualified here: cold compiler/startup cost, four-Worker contention, complete
camera-gesture p95/p99, peak RSS/VRAM, multiple scenes, other browser engines,
quantified image differences or new operator visual acceptance. The same
decoded data is necessary evidence, not a replacement for those broader gates.

## Evidence, serving and rollback

Evidence on local WSL and BIGZEN:
`/home/olivier/droneai-qualifications/gstile-fused-decode-20260827`.
Retain archived baseline, all candidate patches/modules, manifest and original
packs/payloads, benchmark HTML/Worker/scripts, raw Node/Chrome reports, red and
green tests, typecheck/static/lint, screenshots and build/deployment logs.
The initial fixture-preparation script used `tile.packId` instead of the
actual `tile.pack`; it failed before copying a pack and is retained as
`prepare-benchmark-v1-failed.mjs`. No failed experiment was deleted.

The separate local diagnostic page is `http://127.0.0.1:3011/benchmark.html`;
`benchmark-worker.js` reads only these archived fixtures/modules. To reproduce:
serve the evidence directory on loopback with Python's HTTP server and press
the comparison button in a browser supporting native Float16Array. The Node
equivalent is `node compare-node.mjs reuse-exp` from the evidence directory.

Production qualification source archive SHA-256:
`a7e93af1aa10dc4c9d70fc7356efbc0dfc3bb92abf33c2a296f0228d63f3e5b8`.
Image `droneai-frontend:a7f0cd0`:
`sha256:2ce63ad22007146523143aa356279887bb5aeb249cc0b716e358f4d0d854da5a`.
Container `droneai-frontend-gstile-a7f0cd0`, existing loopback port 3000.
Bundle and fixture server unchanged. Previous container/image
`droneai-frontend-gstile-705b81c` is retained. To roll back after verifying IDs,
stop only the new qualification container and restart that previous one.

Next substantial work remains reducing Worker-result/merged staging copies
with explicit ownership and memory limits, and measuring complete camera paths.
No new cache or shared-memory deployment policy is introduced by this lot.
