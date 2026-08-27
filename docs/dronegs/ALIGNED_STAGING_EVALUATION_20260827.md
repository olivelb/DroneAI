# GSTile — aligned staging evaluation, 2026-08-27

## Scope and decision gate

**Proceed to a bounded renderer integration, but do not enable this prototype
as a production optimization yet.** Including staging allocation/uploads, the
paired median host time falls by 19.57% at 1.29M and 16.30% at 7.27M; GPU queue
completion also improves. Unlike the earlier direct-upload variants, this
component experiment improves both measured scopes. It still does not establish
a complete-renderer/FPS/network-loading gain.

Baseline: `5636e56c89dbb25f097e1b3ab1053d691ed2e1fa` (PR #273), clean
authoritative WSL checkout. The user's subsequent `ok` accepts that previous lot;
it is not visual acceptance of this standalone prototype.

This experiment changes the **shape of temporary staging textures**, keeping
the existing persistent destination arena and every active coefficient intact.
The square/near-square reference staging width differs from the arena width,
forcing many one-row copies. Using the arena width for staging lets consecutive
source rows be copied as at most two vertical strips, with up to two partial
copies at either end: **at most six rectangles per linear range**.

No application code, shader, bundle, cut, selection, sort, quality setting,
dependency, image or running container is changed by this evaluation. It does
not enable the previously rejected direct `writeTexture` arena path; both arms
still upload temporary textures and copy into the persistent arena. No GPU
compute scatter or new shader is needed by this experiment.

The [PlayCanvas Texture.copy contract](https://api.playcanvas.com/engine/classes/Texture.html#copy)
allows same-format rectangular copies with no scaling. Installed PlayCanvas
2.21.4 `GSplatStreams.init/resize`, `WebgpuTexture.copy` and
`uploadTypedArrayData` were inspected and retained. Alignment is an experimental
layout choice, not an engine performance guarantee.

## Frozen component protocol

- Windows Chrome 151.0.0.0, reported GPU adapter `nvidia` / `lovelace`.
- Intel i9-13900H, 14 cores / 20 logical processors, 34,048,679,936 physical
  RAM bytes. RTX 4070 Laptop, driver 610.62, 8,188 MiB device memory reported
  by WSL `nvidia-smi`; these are capacity figures, not measured peaks.
- WSL Linux 5.15.167.4, Node 24.14.0; pinned PlayCanvas 2.21.4 source contract.
- Synthetic deterministic populations: 1,288,047 and 7,265,600 splats. These
  are not actual recorded Saint-Etienne slot allocations or camera gestures.
- Source-contiguous ranges contain 16,384–32,767 records, destination starts
  at 17, with a 13-record gap after every eight ranges. Respectively 52 and
  297 ranges; no node-range coalescence is applied in either arm.
- Destination: 2,739 × 2,739 in both arms. Reference staging: 1,135 × 1,135
  and 2,696 × 2,695. Candidate staging: 2,739 × 471 and 2,739 × 2,653.
- Eleven streams: two RGBA16F, five RGBA32UI, four RGBA32F, 160 bytes per
  splat. Finite deterministic active bit patterns; zero padding. Usages include
  COPY_SRC, COPY_DST, TEXTURE_BINDING and RENDER_ATTACHMENT, matching the
  pinned engine for these streams.
- Two warmups per arm, six alternating AB/BA pairs **per profile and size**,
  100 ms after each arm. No forced GC, no outlier removal, no concurrent
  viewer/build. OS scheduling, clocks and garbage collection are uncontrolled.

Reference is the exact compiled shared-copy helper and planner from the baseline.
Candidate uses the same helper with only its planner import changed. The
experimental equal-width planner reorders disjoint rectangles within a range;
stream and range order remain unchanged. No overlapping destination rectangle
is allowed, and no source/destination aliasing is tested or used.

Two distinct timing scopes:

1. **Copy only:** both sets of staging textures are already uploaded/fenced
   before timing; planning, encoding and submission are measured.
2. **Upload + copy:** new staging texture allocation and eleven uploads are
   included for every arm. Source CPU arrays are prepared before timing in both
   layouts; main-thread repadding is **not** included or recommended. The
   harness flushes the queue before each upload, modeling the engine's eager
   submission order, but does not execute a PlayCanvas graphics device.

Destination textures persist but are **fully cleared and fenced before every
arm**. Previously correct resident values cannot conceal an omitted copy. Host
time ends after submission; complete time extends through
`queue.onSubmittedWorkDone()`, not a GPU timestamp. Readback, hashes, source-array
generation, clearing and retirement/destruction are outside timing. No rendered
frame, world reset, sort, Worker assembly or engine resource/entity construction
is measured. Do not combine the profiles or compare their absolute times with
older, differently scoped experiments.

## Results

Times are medians in milliseconds. Changes are medians of the six paired
ratios, not ratios of the displayed medians. Six pairs are not enough to
establish p95/p99 latency or broad hardware confidence.

| Scope | Splats | Host reference / candidate ms | Paired host change | Complete reference / candidate ms | Paired completion change |
|---|---:|---:|---:|---:|---:|
| Copy only | 1,288,047 | 20.55 / 2.10 | -89.09% | 101.75 / 21.85 | -78.39% |
| Copy only | 7,265,600 | 63.45 / 18.60 | -70.40% | 390.20 / 117.35 | -69.58% |
| Upload + copy | 1,288,047 | 57.45 / 45.65 | -19.57% | 265.95 / 194.70 | -22.34% |
| Upload + copy | 7,265,600 | 284.70 / 231.05 | -16.30% | 1,069.60 / 880.05 | -19.39% |

Paired median absolute host savings with uploads included are 11.45 and
47.80 ms respectively. Copy commands fall from **18,216 to 2,838**, and from
**62,062 to 16,313**. The active copied bytes are unchanged: 206,087,520 and
1,162,496,000 bytes per arm. Both upload profiles still issue eleven writes.

Changing row width alters padding slightly. Staging allocation/upload payload
increases from 206,116,000 to 206,411,040 bytes at 1.29M (+295,040 bytes), and
from 1,162,515,200 to 1,162,650,720 at 7.27M (+135,520 bytes). These are logical
texture/payload formulas, not observed browser/driver RSS/VRAM. The destination
arena stays at 1,200,339,360 logical bytes. There is no reduction of downloaded
bundle bytes or numerical precision.

## Correctness

CPU tests exhaust all valid offset/count combinations for widths 1–12, source
height 4 and destination height 5, plus 1,000 seeded boundary cases: **186,016
exact mapping cases**, with eight invalid-input checks. An independent linear
index oracle verifies coverage, bounds, no duplicate destination writes and
the six-rectangle bound. These are correctness tests, not a CPU benchmark.

For every GPU arm including warmups, all eleven full destination textures are
read back using 256-byte-aligned pitches. Padding is stripped and SHA-256 checked
against an independently generated CPU scatter target, including untouched gaps
and tail. Source CPU hashes are checked before/after each series; validation
errors fail the run. The Node validator independently reconstructs fixtures and
hashes, expands both plans into linear source-index maps, and checks exact
coverage, counters, sample order, dimensions, summaries and source provenance.

All **704 texture-hash checks** pass across 64 arms (48 measured, 16 warmup).
These repeat two deterministic size fixtures across two scopes; they are not
704 independent scenes. The first independent-validator run rejected a summary
rounding discrepancy around 4e-15 ms. The original script/error and unchanged
raw results are retained. `validate-v2.mjs` allows at most eight machine epsilons
scaled by max(1, absolute value) for derived statistics only. Texture hashes,
mapping, dimensions, counters and provenance still require exact equality.

These checks do not qualify a browser/GPU matrix, production retirement,
cancellation, device loss or partial-update recovery. No image metric, new PLY
visual acceptance, real door→facade replay, input latency or product FPS result
is claimed.

## Required integration before promotion

The application currently assumes square-packed texture capacity in several
ownership/validation boundaries. It is not sufficient to change a GPU width:

1. Carry explicit staging dimensions through column allocation, assembly Worker
   initialization/response and native-column validation. Retain the existing
   square layout for individual decoded tiles and promoted persistent arenas.
2. Account for padding in the existing 2 GiB destination budget, while retaining
   the 128 MiB decode working budget, four-task limit and twelve-owned-buffer
   invariant. Never hide extra backing storage with a permissive subarray.
3. Set matching engine stream dimensions before adopting native arrays, without
   copying all streams again on the main thread. Retain the generic different-
   width planner for other supported copying paths.
4. Test partial rows, fragmented residents, extra diagnostic streams, Worker
   failure/fallback and stale/aborted cuts. Keep publication between rendered
   frames and exact centers/bounds/cut identity.
5. Qualify the integrated viewer on actual recorded arena ranges, fixed camera
   paths and controlled cache states; measure commit and frame-time tails as
   well as copy cost. Require original-PLY visual acceptance before promotion.

## Retained evidence and operational state

Directory in WSL and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-aligned-copy-20260827`.
It contains the baseline archive, exact reference modules, experimental planner,
executable pages, frozen fixtures, tests, independent validator, raw JSON,
source/module provenance, engine sources/license, environment and SHA manifest.
The README gives exact loopback URLs and reproduction commands. No previous
experiment, image, container or dataset is deleted.

This is a documentation-only repository delivery. Native/CUDA tests and frontend
builds are deliberately not rerun for an unchanged application tree. The
qualification viewer continues using the already deployed PR #273 frontend.
