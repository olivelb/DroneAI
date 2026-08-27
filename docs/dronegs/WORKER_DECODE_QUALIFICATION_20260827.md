# GSTile Worker recovery and decode telemetry — 2026-08-27

## Decision and scope

Ship the bounded Worker failure fixes and measurement breakdown. This is a
robustness/observability increment, **not a demonstrated normal-path speedup**.
No shader, camera, selection, transition, Q96, SH, opacity, bundle, GPU budget,
timer-yield policy or native DroneGS version changes. Merged remains the default.

Code qualified: `705b81cf520c1de6421bc7e4975e4bc72a1c6710`, based on
`b9715f1cb5a045f9e8d036b75f585976079225dd`. The previously served frontend
`abcde8e85b3c23a6b00743ab8e3be4ca680ac18e` has identical frontend sources to
that base. Documentation-only follow-up commits do not require a rebuild.

## Bugs reproduced and fixed

- A synchronous payload slice or `postMessage` failure could leave a Worker
  slot occupied. Dispatch now settles the task, retires that Worker and drains
  queued work through the replacement without recursive drain calls.
- Partial pool construction could leak Workers created before a factory
  failure. Construction now terminates those Workers before rethrowing.
- Replacement creation failure could strand queued promises. The pool now
  disposes and rejects every pending task, including tasks on other slots.
- Queued cancellation now immediately releases the queued task/reference.
  An already-running cancelled decode still occupies its slot until its reply;
  no unbounded extra Workers or concurrent payload copies are introduced.

The renderer's existing fallback policy remains: a non-cancellation Worker
failure disables its pool and uses the native synchronous fallback. Pool-level
recovery tests do not imply that the renderer silently retries that same tile
on a new Worker. No browser failure was injected in the real Saint-Etienne run.

## Metric contract

`lodDecodeBreakdown` is in the debug snapshot, render statistics and the
viewer's `data-lod-decode-breakdown` attribute. The developer HUD displays the
queue, compute and input/output copy durations. A fresh accumulator is used
per cut; the completed load publishes the breakdown with existing LOD metrics.

| Field | Meaning |
|---|---|
| `workerTasks` | Number of successful Worker responses accumulated for this cut. |
| `queueMs` | Sum from enqueue to slot admission, before input copying. |
| `inputCopyMs`, `inputCopyBytes` | Sum of explicit defensive input-copy time/bytes: 96 bytes per record. Originally slices; subsequent bounded recycling also uses `.set`. Successful Worker work plus any synchronous fallback slice; failed dispatch copies are not counted. |
| `roundTripMs` | Sum from immediately before `postMessage` to main-thread response handling, including scheduling, transfer/serialization and Worker computation. |
| `computeMs` | Sum of decode/stream preparation durations measured inside each Worker, including decoder allocations. It is nested within round-trip, not an additional wall-time phase. |
| `outputCopyMs`, `outputCopyBytes` | Explicit active-stream copies into merged staging columns, including fallback results: 172 bytes per record. Padding and browser/driver-internal copies are excluded. |

These are **task sums, not gesture latency or GPU elapsed time**. Cancelled and
failed Worker compute durations are not returned or counted. Existing Worker
service, CPU decode and upload measurements retain their earlier semantics;
CPU decode includes output copies, and upload is host-wall submission time.
Queue sum can greatly exceed the cut wall time when many tasks await four
Workers. No new GPU fences, SharedArrayBuffer or cross-origin-isolation policy.

Each realm computes its own elapsed duration; no subtraction between Worker
and Window time origins. The input pack belongs to the range cache: detaching
it to remove a copy would break reuse. These choices follow the documented
[clock origins](https://developer.mozilla.org/en-US/docs/Web/API/Performance/timeOrigin)
and [transfer ownership](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API/Transferable_objects).

## Automated qualification

- Red gate: two new tests fail on the former code (dispatch slot leak and
  partial-constructor leak); retained in `red-worker-tests.log`.
- Final frontend suite: **243 tests / 35 files pass**. GSTile subset: 192 tests.
- Pool tests cover synchronous dispatch failure, partial construction,
  replacement failure with queued/other active work, detached queued buffers,
  active/queued cancellation, stale/malformed-timing/message-error responses,
  copy ownership, invalid counts and deterministic clock accounting.
- Existing 16,384-record native decode oracle passes exact typed-array and
  bounds equality against the existing decoder/packer. The bounded copy test
  asserts 172 active bytes, excluding texture padding. All existing colour,
  SH, transform, cache, network recovery and backend tests also pass.
- Typecheck and targeted ESLint pass (two unchanged pre-existing unused `pack`
  warnings in the backend). `make static PYTHON=.venv/bin/python` passes.
- Production Next.js build passes on BIGZEN using the unchanged pinned Docker
  base and dependency lock. No dependency updates.

## Browser observations, not a performance comparison

Chrome on the user's PC; production frontend served on BIGZEN through the
existing localhost tunnels. Canvas 1410 x 638 drawing-buffer pixels, FOV 42,
budget 7.5M, merged. Bundle identity remains
`sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.

Initial reference and candidate reloads select exactly the same 374 node IDs
and target count. A successful shifted drag, then three alternating wheel
gestures (-800/+800 at viewport 950,760), exercise detail/facade refinements.
The endpoint node sets match on all three repetitions: detail 319, facade 365.
This is an exploratory **facade-detail** path, not a frozen world-space
door-to-facade production benchmark. Each of eight candidate snapshots has
zero pending nodes, zero Worker fallbacks and exact input/output byte formulas
relative to its added Gaussian count. The retained validator checks these
contracts and initial/endpoint selections.

Rounded observations below are the **last committed cut** at each endpoint:

| Observation | Added splats | LOD / load / commit ms | Worker compute sum ms | Input / output copy sum ms | Resource / upload ms |
|---|---:|---:|---:|---:|---:|
| Reload | 7,265,600 | 3059 / 2634 / 407 | 6802 | 257 / 502 | 114 / 96 |
| Facade return 1 | 2,918,247 | 1057 / 922 / 132 | 2672 | 97 / 191 | 49 / 28 |
| Facade return 2 | 3,290,509 | 1253 / 1038 / 204 | 2984 | 103 / 203 | 42 / 20 |
| Facade return 3 | 3,029,348 | 1190 / 1033 / 153 | 2824 | 98 / 187 | 35 / 29 |

At the candidate reload snapshot, all 244,311,197 observed network bytes are
classified as prefetch, with 404 persistent-cache hits. Yet the selected cut
still takes 3.06 s, and its explicit input/output copies total 758.8 ms over
1.95 GB of logical payload. This supports investigating CPU decode/copies and
cache-read scheduling in addition to transport. Background prefetch can still
contend for resources; this does not establish that the network has no impact.

Limitations: cache state is naturally warm and changes between runs; initial
reference load was 4.94 s with a different cache mix, so **do not claim a 38%
speedup**. Smooth gestures can finish intermediate cuts; added splat counts
therefore differ despite identical endpoints. The snapshot is not the full
gesture, and cumulative scheduler metrics include prefetch. Three observations
are not a p95/p99 study. Long tasks, peak RSS/VRAM, Firefox/Safari, other scenes,
image-difference metrics and full production PLY visual acceptance for this
exact source were not measured here. A viewport screenshot confirms that the
candidate renders; it is not a quantified PLY comparison.

Several browser scroll commands and one full-page screenshot timed out; their
page state was inspected before continuing. One fullscreen request on the
reference was denied (`not granted`); no browser security setting was changed.
The successful viewport screenshot and every raw observation are retained.

## Next experiments, ordered by observed potential

1. Reduce Worker-result to merged-column copies, or reuse decoded streams with
   a strictly bounded cache. Test logical bytes, peak CPU/VRAM and exact stream
   parity: adding a second large cache is not free memory-wise.
2. Profile and optimize the Worker decoder itself (allocation reuse, then
   SIMD/WASM if justified). Compute is substantial even with cached packs;
   preserve the exact Float32/half-float and SH oracle, including padded lanes.
3. Break down cache-read versus HTTP queue time and keep critical demand ahead
   of prefetch. The old fetch sum alone does not identify network latency.
4. Freeze world-space camera paths and collect every cut, cancelled work, UI
   long tasks and frame-time distributions. This is a prerequisite to promoting
   any of the above as a responsiveness gain. GPU submission timing alone is
   insufficient to qualify driver/GPU upload cost.

Do not remove the pre-decode event-loop yield or transfer the cached input
buffer speculatively. Neither change is promoted by this lot.

## Retained evidence and rollback

Follow-up: [exact scale-exponential reuse](NATIVE_DECODE_EXP_REUSE_20260827.md)
reduces isolated Chrome Worker decode time by 6.49% in the recorded paired
benchmark. Two SH-loop variants were rejected. A subsequent
[bounded assembly Worker](WORKER_ASSEMBLY_QUALIFICATION_20260827.md) moves the
large output copies away from the main thread with byte-identical outputs.
Subsequent [bounded input recycling](INPUT_RECYCLE_QUALIFICATION_20260827.md)
reduces fresh input allocations while preserving those defensive copies and adds
allocation/reuse counters. Full camera-path latency remains a separate target;
neither component result is a universal full-load/FPS speedup.

Local WSL and BIGZEN evidence directory:
`/home/olivier/droneai-qualifications/gstile-worker-latency-20260827`.
Contains red/green tests, final tests/static/typecheck/lint, source archive,
build/deploy scripts and logs, all baseline/candidate JSON snapshots, viewport
PNG and `inspect-observations.mjs` / `observation-contracts.log`. An initial
snapshot export syntax error is retained as `baseline-capture-error.txt`, not
treated as valid JSON or discarded. Do not delete failed experiments or logs.

Source archive SHA-256:
`afbd81d13dff8e48f65084261cd24ab9ba62a5107568bcf570b5aff7c85244e8`.
Image `droneai-frontend:705b81c`:
`sha256:22765355977747d0b37af6ec4117c6c10d4e59e535c680e6dd06ff38e4bf973f`.
Active qualification container: `droneai-frontend-gstile-705b81c`, loopback
port 3000, restart unless stopped. Fixture server/bundle unchanged.
Previous container `droneai-frontend-gstile-abcde8e` and image are retained.
Rollback: verify the names/image IDs in `deployment.txt`, stop the candidate
and start that previous container; no source or bundle deletion is necessary.
