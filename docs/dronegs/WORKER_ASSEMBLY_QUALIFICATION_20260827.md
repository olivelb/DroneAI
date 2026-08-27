# GSTile — bounded off-thread merged assembly, 2026-08-27

## Scope and invariants

Reference: `2a07d5c9785f5d401a30acdd5d43055813af0fb9` (PR #269).
Qualified implementation candidate: `c352fa224aad3523887e2bba2376544f36a6dcb1`.

Large merged cuts now have one dedicated assembly Worker owning the existing
destination columns. The four decoder Workers return native streams as before;
their owned output buffers are immediately transferred to the assembler, which
performs the same `copyGsTileNativeResult` operation. At completion the single
destination is transferred back for the unchanged PlayCanvas staging/arena
commit. No shader, native decoder arithmetic, SH encoding, opacity, culling,
sort, cut selection, budget, fixture or tiler change. Merged remains the default.

The range-cache input is never transferred: its existing 96 B/splat defensive
copy stays. The 172 B/splat output copy is **moved off the main thread, not
eliminated**. GPU staging/upload and the subsequent 12 B/splat arena-center copy
are unchanged. ArrayBuffer ownership transfer is the standard browser mechanism,
not SharedArrayBuffer: no COOP/COEP or security-header change. See
[transferable objects](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects).

An explicit plan gives each compact range exactly one write. Missing, duplicate,
wrong-sized or out-of-plan writes fail before a result can be published. Source
streams must have the correct types/lengths, twelve separate full-size owned
ArrayBuffers, and no oversized backing allocation. Padded output lanes remain
zero; all active lanes and exact Float32/half/SH bits follow the existing copy.
Per-node bounds are captured before transfer and retain the original union path.

## Admission, cancellation and fallback

Enable only for >=2,000,000 **added** splats, a native decoder Worker pool,
destination streams <=2 GiB and tiles fitting the admission cap. Smaller cuts,
float32 diagnostic transforms, unavailable Worker/native-Float16 environments
and oversized cases keep the supported main-thread path. No quality reduction
or smaller cut is used to meet these limits. `gstileWorkerAssembly=0` explicitly
disables only this off-thread path for A/B/rollback, without changing rendering.

One permit covers decode input allocation, decode, result transfer and copy
acknowledgement: at most four permits and 128 MiB of estimated live input+output
payload. Weight = `96*n + 12*n + 160*textureCapacity(n)`. Fetch/SHA remain under
the existing scheduler; they are not throttled to four requests. A FIFO admission
queue stores pending requests, not decoded results. The one full destination
replaces the previous full destination allocation, rather than adding a second
large cache. Bounds cover algorithmically live owned buffers; GC, browser copies,
network cache, GPU memory, thread overhead and peak process RSS are not included.

A superseding camera selection terminates its assembler, rejects queued permits
and pending RPCs, and prevents publication. A protocol/postMessage/Worker error or
30-second timeout closes the owner. For a still-current, live selection the backend
cancels pending work, disables assembly for its lifetime and retries once on the
existing main-thread path. Failure occurs before GPU staging/commit, so the last
complete representation is retained. Stale failures cannot trigger a retry or
overwrite the newer cut's error state. Corrupt input or unrelated GPU failures
are not turned into successful empty output.

Telemetry preserves main-thread `outputCopyMs/Bytes` and adds separate
`assemblyWorkerMs`, `assemblyTransferMs`, `assemblyBytes`, `assemblyAdmissionMs`,
`assemblyPeakBytes` and `assemblyPeakTasks`. Times are per-cut task sums, not
additive wall time. Debug snapshots additionally expose backend-lifetime
`assemblyWorkerFailures` and `assemblyWorkerDisabled`. The UI distinguishes
assembly, transfer and admission instead of hiding the moved copy cost.

## Benchmark protocol

Immutable Saint-Etienne bundle:
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Three original SHA-verified fixtures: `r` (16,384), `r01001001000` (32,687),
`r1100100010000` (65,536). Full fixture/quantization/payload hashes are retained.
The same records are repeated to create controlled cut sizes, not new scan data
or captured camera trajectories. Windows Chrome 151, hardwareConcurrency 20.

Both arms use the unchanged real decoder Worker pool and identical main-thread
input slices. Four task lanes await consumption before taking another tile;
this controlled component schedule is not the production network scheduler.
Reference allocates/copies merged columns on the main thread. Candidate allocates
in the assembly Worker, holds the byte-counted permit through copy acknowledgement,
and transfers the full result back. Candidate Worker creation, allocation, admission,
decoding, input copies, transfer, output copy and final handoff are timed.
Fixture network fetch, hash/oracle checks, GPU upload and rendering are not.

Two warmup passes per arm, alternating AB/BA pairs, no outlier removal or forced
GC. Output SHA-256 for all twelve complete buffers (including padding) and bounds
must match on **every** sample; input hashes are checked again after the run.
The prototype uses six pairs per size to select a threshold. Integrated code
is separately transpiled with the repository TypeScript; its source/module hashes
are retained. The JSON `source` field identifies the reference; the candidate
is bound to c352fa2 by `candidate-module-provenance.json` and validation.

The harness also records 10 ms timer gaps and browser long-task entries inside
the measured window. These are diagnostic responsiveness signals, not frame times
or a deterministic input-latency metric. System activity and GC are uncontrolled.
Repeated pairs on this machine are not independent devices or confidence intervals.

### Prototype threshold selection (not the final integrated-code results)

| Repeated records | Reference median ms | Assembly Worker median ms | Paired wall change | Decision |
|---|---:|---:|---:|---|
| 1,031,463 | 266.25 | 293.85 | +13.12% | Keep small cuts on main thread |
| 2,062,926 | 536.50 | 539.50 | +0.38% | Candidate threshold 2M |
| 3,208,996 | 811.85 | 823.10 | +2.65% | Proceed to integrated qualification |

The prototype removes ~98% of the explicitly timed main-thread assembly work,
not 98% of loading or rendering. It does not establish universal optimality of
the threshold. Every trial, including the rejected 1M activation, is retained.

## Automated and fault qualification

### Integrated component results

All 36 pairs below use the compiled c352fa2 client/Worker, not the prototype.

| Records / run | Pairs | Main assembly median ms | Main-thread transfer median ms | Paired main-work reduction | Paired total wall change |
|---|---:|---:|---:|---:|---:|
| 3,208,996 / 1 | 12 | 199.45 | 3.70 | 98.12% | -5.25% |
| 3,208,996 / 2 | 12 | 181.95 | 3.90 | 97.85% | +0.86% |
| 7,449,455 | 6 | 430.85 | 9.35 | 97.88% | -3.62% |
| 2,062,926 | 6 | 117.70 | 2.05 | 98.24% | +1.39% |

These component results support moving the copy away from the UI thread above
the threshold, not a universal loading speedup. Total wall results vary by run.
Median per-sample maximum timer gaps (main vs off-thread) were 85.75/55.40 ms,
68.05/52.90 ms, 128.55/124.40 ms and 51.40/39.60 ms respectively. In particular,
the large case retains >100 ms gaps: input copies, allocation/GC and other work
remain. It would be incorrect to claim 98% less lag or higher FPS.

Largest destination: 1,281,420,660 bytes. Largest tracked in-flight input/output
payload: 70,254,592 bytes; <=4 task lanes, below the 128 MiB cap. These counters
and ownership tests are not peak process RSS/VRAM measurements. The benchmark
does not have the production network cache or GPU allocations live alongside it.

### Checks and drills

294 frontend tests pass, including 24 new cases: exact outputs/padding with
out-of-order arrivals, incomplete/duplicate/invalid ranges, memory and task caps,
detachment, acknowledgement, queued/active cancellation, silent/crashed workers,
construction/postMessage/message errors, stale responses and retry policy.
Typecheck, targeted lint and repository static gates pass. BIGZEN production
Docker build passes with the existing optional-peer npm warnings; prune reports
zero vulnerabilities. No dependency or native DroneGS version change.

Real Chrome Worker drills pass: invalid plan, uncaught Worker crash, actual
30-second timeout, cancellation during copy with queued admission, invalid range
after transfer. Six assembly Workers created, six terminated, zero left live.
The unchanged cached payload re-decodes successfully after these failures;
main-thread fallback output matches all twelve hashes of a fresh successful
Worker assembly. These drills exercise the transport/ownership boundary and
fallback data contract; the production viewer's automatic failure retry is
covered by code/tests, not by injecting a Worker crash into the live viewer.

## Production-build viewer observations

The deployed c352fa2 image loads the same initial selected node IDs as f4b516b:
374 nodes, 7,461,366 splats, one merged entity/resource/placement. Pending work,
decoder fallbacks and assembly failures are zero at the settled snapshots.
The default path and `gstileWorkerAssembly=0` are both exercised on the same
production build. A small pan completes 285,740 additions without an assembly
Worker, as intended (369 selected nodes, 7,477,820 splats, pending zero).

| Observation | Main output copy ms | Assembly Worker ms | Main transfer ms | LOD wall ms |
|---|---:|---:|---:|---:|
| Previous image f4b516b | 589 | 0 | 0 | 6,326 |
| c352fa2 default, first | 0 | 560 | 15.2 | 3,127 |
| c352fa2 explicit main-thread A/B | 509 | 0 | 0 | 2,927 |
| c352fa2 default, repeated | 0 | 535 | 14.7 | 3,047 |

The large update adds 7,265,600 splats in each observation: 1,249,683,200 logical
output bytes now copied in the Worker rather than on the main thread. Tracked
in-flight payload peaks at 58,610,616 / 64,576,744 bytes in the two default
snapshots, with four permits. Input copy remains about 260 ms and 697,497,600
bytes. Admission time is a sum across waiting tile tasks, **not** a 170-second
wall delay. The LOD remains a roughly three-second operation on warm data.

These page loads have different cache/prefetch histories and are not controlled
end-to-end timing pairs. The faster second page load is not evidence of a 50%
loading speedup; the same-build off/on wall times also demonstrate why moving
UI work does not automatically reduce elapsed loading time. No FPS increase,
cross-browser performance claim, peak RSS/VRAM result or quantified PLY image
comparison is asserted. Viewport screenshots only confirm scene rendering;
user visual acceptance of this exact implementation remains separate.

Additional camera rotations return to a completed cut without failure. They
do not prove an in-flight production cancellation (the inspected intermediate
snapshot already had pending zero); deterministic cancellation is qualified in
the real-Worker drill described above. Final page restored to default GPU mode
and initial view. The production renderer/GPU commit paths are unchanged.

## Artifacts and rollback

Evidence retained locally and on BIGZEN:
`/home/olivier/droneai-qualifications/gstile-worker-assembly-20260827`.
Source archives, fixtures, prototype and integrated harnesses, compiled modules,
all raw samples, fault drills, test/build logs and viewer observations remain.
No failed experiment or previous version was deleted.

Candidate source archive SHA-256:
`4fae28daea7ae790ff606309fd8e7846393685fd63fac8f5c87036b2987b2c9a`.
Image `droneai-frontend:c352fa2`:
`sha256:d166370c4351e809947eedc186f88c1cfc3af2a7a46ee3f1e2729aed7801d1d1`.
Previous container retained: `droneai-frontend-gstile-f4b516b`, image
`sha256:3dd537a93c4e7fc6e5a9ef182e9c1d8d71336f10fc6d5037c5152b02abd96c49`.
Rollback: verify these identities, stop the candidate and start that container,
or use `gstileWorkerAssembly=0` for a functional comparison. Fixture server and
bundle are unchanged. Native DroneGS remains dev.72.
