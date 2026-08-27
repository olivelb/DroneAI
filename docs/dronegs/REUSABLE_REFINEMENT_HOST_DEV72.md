# Dev.72 reusable refinement host workspace

Date: 2026-08-27. Follow-up to
[dev.71 exact parallel percentiles](PARALLEL_PERCENTILES_DEV71.md), audit A4.
This changes native host scratch lifetime, not rendering, scientific policies,
checkpoint formats or the production image.

## Design and resource contract

The six download vectors were allocated, zero-initialized and destroyed on
every refinement. A private per-context workspace now retains six vectors:
N 32-byte pruning records and five N-float statistic arrays. Its capacity grows
only when an input population exceeds the previous high-water mark. Active
spans use the current count; all six vectors retain the high-water size.
Compaction shortens only the local active spans; stale tails are never included
in median/scoring inputs. The next invocation overwrites every active element.

Storage uses the same value-initialized vector construction as the reference,
only on first allocation or growth. Repeated same-size or smaller calls neither
allocate nor initialize. Keeping five separate statistic vectors also preserves
their original allocation sizes.
Compile-time assertions retain the exact 32-byte layout and trivial default
construction/copying. There is no custom allocator or raw-storage lifetime cast.
All replacement allocations succeed before swapping old storage; overflow
is rejected first. Allocation exhaustion itself was not fault-injection tested.

All reads follow the existing completed synchronous downloads. No new CUDA
work, allocation, transfer, pinned memory, stream or fence is introduced.
The five statistics still download separately; snapshot plus statistics is
still 52 bytes per Gaussian. The old median helper still makes an independent
vector copy and executes the same algorithm; only its input becomes a span.
Percentile arithmetic, pruning counters, survivor order/fallback, scoring,
Gumbel keys, split and decay are unchanged.

The workspace is not checkpointed. Loading a checkpoint retains scratch
capacity but does not make scratch contents authoritative: the following
refinement downloads the restored state again. It is released on context
destruction. The existing context is not made safe for concurrent calls.

Retained logical payload is **52 times the largest encountered population**,
bounded by the context's Gaussian capacity. At 5M this is **260,000,000 bytes
(247.96 MiB)** retained between calls. Growth can temporarily retain both old
and new allocations; this is a speed/RAM tradeoff, not a memory optimization.
Other temporary vectors keep their previous lifetime.

Preparation timing now measures acquiring spans and any growth allocation and
initialization. Buffer destruction no longer occurs on every return.
Only fenced whole-refinement timing supports a speed
claim. See [telemetry interpretation](REFINEMENT_TELEMETRY.md).

## Frozen protocol and source identity

Reference source: `c84318dcf7fe57249975857f43894db16e8e9148`. This adds only the
benchmark's context-mode option to dev.71 main
`d7020f09a3f5f3bf1074a9618b04bfae40437b1d`; no production algorithm change.
Candidate source: `74a466f4c47e9bba23eb1c461b46b47204ed1c2d`, clean when
configured. Later documentation does not change the qualified native code.
Both binaries use the identical benchmark source from the reference commit.

The optional checkpoint benchmark accepts a final `fresh|reuse` argument;
omission preserves `fresh`. Reuse retains one context but reloads the same
complete checkpoint before **every** call. It does not repeatedly refine an
evolving state. Mode is recorded in JSON. Both modes save the excluded warmup
output; reuse also saves the final measured output for full-state comparison.
Input load/checksum, context setup/destruction and saves are outside the timed
interval. In fresh mode the candidate's cached-buffer release moves to that
untimed context destruction; this is not a full create/refine/destroy benchmark.
The existing benchmark-only device fence includes completion, unlike the
production host-wall telemetry.

Real input is the same 5M Saint-Etienne door checkpoint used in dev.71:

```text
/home/olivier/droneai-door-retraining-saint-etienne-1mm/checkpoints/porte-strict-saint-etienne-1mm/full/training.ckpt
```

Reference-absolute/FastGS, SH3, opacity SH on, seed 42, 30,000 completed steps,
4,540,000,774 bytes. SHA-256:
`2f33e7572dc749fbc07cce3ade9390cd9f25729e73d7a4751aa250588c8a26c9`.
This historical mode does not change Production V1's scalar-opacity default.
The manual final-state diagnostic has reset statistics: zero growth candidates,
120,986 pruned and 4,879,014 survivors. It is not an actual scheduled training
iteration or a full-training benchmark.

Group order: reference reuse (5 measured), reference fresh (3), initial variant
reuse (5), initial variant fresh (3), reference reuse again (3), six synthetic
groups, corrected variant reuse (5), corrected variant fresh (3), reference
reuse again (3), six further synthetic groups; the final six-vector variant
then repeats reuse (5), fresh (3), reference reuse (3) and six synthetic groups.
Each has one excluded warmup.
Candidate compilation/tests occur between groups, not during timing.
Groups are sequential, not randomized pairs.
Fresh denotes a fresh context, not a cold filesystem/browser cache.

For each variant, six scalar-opacity synthetic groups use the retained 32,768-input
no-growth, compaction/split and recycling checkpoints, with one excluded warmup
and five measured reuse calls per arm. First and final outputs are retained.

## Results

### Rejected allocation variants

Commit `4f19accab0139ede185ac1fb18cd8b64331c6ebf` used uninitialized allocations.
It improved reuse to 0.271988 s, but fresh-context median regressed to
**0.374598 s versus 0.315586 s (+18.70%)**. Snapshot/download median rose from
0.028754 to 0.129237 s, while preparation became almost zero. This is consistent
with first-touch/page-fault costs moving into pageable downloads, not proof of
a faster transfer. No page-fault attribution experiment was performed.
All saved outputs still matched; correctness alone was insufficient to retain
this variant. Its complete evidence and outputs remain available.

Commit `16ab13d5c0173246ff21cab95bacde4834bfa1cf` restored initialization while
retaining the combined five-statistic allocation. Reuse was 0.274607 s, but
fresh-context median remained 0.398359 s (+26.23%), so this variant was rejected
too. Moving costs between preparation and download did not fix overall latency.
The final implementation preserves all six original vector allocations and
only extends their lifetime. Allocator thresholds/cache/page-fault effects
were not individually isolated; these experiments do not establish a portable
causal model of the allocator.

### Corrected variant

| Real group | Median fenced refinement (s) | Min–max (s) |
|---|---:|---:|
| dev.71 reused context, A | 0.319771 | 0.310614–0.321329 |
| dev.72 reused context | 0.277208 | 0.273191–0.290201 |
| dev.71 reused context, B after final candidate | 0.322645 | 0.303974–0.323352 |
| dev.71 fresh context | 0.315586 | 0.301608–0.322890 |
| dev.72 fresh context | 0.314901 | 0.311994–0.320184 |

Reuse reduces isolated refinement time by **13.31% (1.15x)** against reference
A and **14.08%** against the trailing reference B. The observed reuse ranges
do not overlap. Fresh-context medians differ by
only -0.22% with overlapping ranges: no meaningful fresh-context gain or
regression is established. Excluded first calls were 0.385776/0.363560 s for
candidate reuse/fresh, versus 0.369276/0.373722 s for reference reuse/fresh.
Those single observations are not a first-invocation speed claim.

| Phase median / payload, reused contexts | dev.71 A | dev.72 |
|---|---:|---:|
| Host preparation (s) | 0.062466 | 0.0000006 |
| Snapshot projection/download (s) | 0.028058 | 0.029391 |
| CPU pruning including percentiles (s) | 0.130508 | 0.152180 |
| CPU compaction (s) | 0.017295 | 0.017297 |
| Compaction upload (s) | 0.001950 | 0.002119 |
| CPU scoring (s) | 0.017784 | 0.017707 |
| Device submission, not GPU elapsed (s) | 0.007848 | 0.006870 |
| Other / host destruction (s) | 0.011771 | 0.006724 |
| Public call before completion fence (s) | 0.278227 | 0.229704 |
| Snapshot download bytes | 260,000,000 | 260,000,000 |
| Compaction upload bytes | 19,516,056 | 19,516,056 |

The unchanged pruning phase is slower in this run; the individual cache,
allocator and scheduling effects were not isolated. The net measured gain is
smaller than the removed preparation cost. Phase medians need not sum to the
median total. The candidate's public call returns with roughly 48 ms of GPU
work still pending in this fixture: **0.277 s, not 0.230 s**, is the comparison
basis. No production synchronization was added to align these numbers.

Whole-benchmark peak RSS for reuse is 4,455,024 KiB for reference A and
4,707,372 KiB for the candidate: **+252,348 KiB (246.4 MiB)**. This includes
load/save and is consistent with the retained 260 MB payload, but is not an
isolated refinement or full-training memory peak. Fresh-mode peaks are
4,455,308 KiB for the reference and 4,707,540 KiB for the candidate: saving
the first output now happens while cached buffers remain resident. Context
destruction releases them. No whole-training RAM measurement was performed.

The main protocol saves **54 complete checkpoints** across the reference and
all three candidate experiments; all compare byte-for-byte with retained
pre-change outputs (18 real and 12 for each of the three synthetic cases).
The final candidate's protocol plus the initial reference accounts for 20 of
these, including first/final reused outputs. Real output SHA-256 is
`743e10f97750b8bc8014a6541f65aeee9cc8dd2f60b1e79c4600aac466f987fe`
(4,430,145,486 bytes). The input hash remains unchanged before/after all runs.
These are entire checkpoint comparisons, including optimizer state, not just
Gaussian arrays or render images. The additional small-case repetition below
saves another **24 byte-identical outputs**, for 78 retained outputs in total.

Initial synthetic medians (five samples per arm) were 2.395 → 2.398 ms for
no-growth, 4.148 → 4.402 ms for compaction and 2.699 → 2.775 ms for recycling.
The compaction change was +6.13%, with overlapping ranges, so two additional
process pairs were run per case in candidate/reference then reference/candidate
order. Each process again restores the frozen input, excludes one warmup and
measures five reuse calls. No implementation or threshold was tuned afterward.

| Synthetic case, pooled 15 measured calls per arm | dev.71 median (ms) | dev.72 median (ms) | Candidate change |
|---|---:|---:|---:|
| No growth | 2.405896 | 2.379797 | -1.08% |
| Compaction + split | 4.448495 | 4.456494 | +0.18% |
| In-place recycling | 2.722697 | 2.749496 | +0.98% |

All pooled ranges overlap. These short groups do not establish a small-case
speedup or a precise no-regression bound; the initial +6.13% is not reproduced
in the pooled comparison. Individual process medians and raw samples remain
available, rather than treating 15 within-process samples as 15 independent
process replications. No-growth changes 0/0, compaction prunes 2,048 and adds
18,432, recycling prunes/adds 128. All full-state comparisons pass.

## Validation and hardware

- Eleven CPU size steps cover empty/first use, growth, shrink and reuse, plus
  per-step repeated acquisition/shrink/regrowth, independent contexts and
  overflow rejection without losing the previous allocation. Exact capacity,
  active span size, independent arrays and pointer retention are checked. Initialized
  snapshot bytes and signed-zero/NaN/infinity/subnormal float sentinels survive
  reuse unchanged. Tests initialize their own active sentinels before reading.
- 24 CUDA cases (six population sizes times bounded/FastGS and opacity SH
  off/on) restore changing checkpoints into one cached context and compare
  topology decisions plus entire saved files with fresh contexts. Fixtures
  exercise hard pruning and the all-pruned survivor fallback. Existing suites
  cover real training, growth/split, recycling and resumed optimization.
- Eight native suites pass on RTX 4070 Laptop/CUDA 12.9 (4.90 s) and RTX
  3090/CUDA 12.0 (3.72 s). CPU ASan/UBSan and CUDA memcheck report no errors.
- 22 targeted Python contract tests and repository static checks pass.
- Portable compilation passes sm_75/80/86/87/89/90/100/101/120. Only sm_86 and
  sm_89 were executed; only sm_86 has real-checkpoint timings.
- The main protocol's 160 telemetry objects and 72 additional small-case
  objects pass the strict schema and
  phase-sum/transfer-byte/measured-call checks. All measured post-warmup device
  free-byte deltas are zero; this is not a peak-VRAM measurement.

BIGZEN: WSL Ubuntu 24.04, Ryzen 9 5950X, 94 GiB RAM, RTX 3090 24,576 MiB,
driver 591.74, about 675 MiB desktop GPU use before the reference. CUDA
12.0.140, CUDA host GCC 12, C++ GCC 13.3, CMake 3.28.3, Ninja 1.11.1, Python
3.12.3, Release/native sm_86. Existing extracted JPEG headers; no system install.
No other qualification/training workload was started on BIGZEN during timings.

Local: i9-13900H, RTX 4070 Laptop 8,188 MiB, driver 610.62, CUDA 12.9.86,
GCC 13.3, Release/native sm_89. Retained Docker image
`dronegs-dev:pixel-weighted-6865308`, ID
`sha256:2c74d65b960f1c867b7ac3c019666d71379e2b7c7066c5736716a9396d966edf`.
Manual hardware qualification is separate from CPU CI; a skipped GPU CI job
must not be presented as executed.

## Reproduction and retained evidence

BIGZEN source worktrees, native builds, scripts, JSONL, hashes and resource logs:
`/home/olivier/droneai-qualifications/reusable-refinement-host-20260827`.
Output checkpoints are on local I: to avoid the nearly full WSL disk:
`/mnt/i/DroneAI-Qualifications/reusable-refinement-host-20260827`.
All old inputs/outputs remain intact. Large-block `dd` reads piped into `cmp`
avoid small drvfs reads; `pipefail` preserves read/comparison errors. SHA-256
is also computed over all bytes. Verification and storage I/O are untimed.

Local source archive, exact native/portable builds, test/sanitizer logs,
mirrored BIGZEN logs and analysis:
`/home/olivier/droneai-qualifications/reusable-refinement-host-20260827-exact-v3`.
Rejected variants' exact evidence remains in siblings `-exact` and `-exact-v2`;
preliminary tests and transfer records remain in `-green`, `-green-v2`,
`-green-v3`, `-cpu` and `-transfer`.
Source archive SHA-256:
`b16d93be35f227d2b841bf4cec2ede9afc6fef65975be6cd1b48f06333f060c9`.

| Binary | SHA-256 |
|---|---|
| dev.71 checkpoint benchmark, 3090 | `f0e0895bc02fa84b71f7ddc394519e621847cb86dc13554a51cd5cd05274f0e6` |
| dev.72 trainer, 3090 | `9121d457c09ec7d10827b71cb44b1aa44e1eb9e2e3d2ea4c223e3d9219a4e2a7` |
| dev.72 checkpoint benchmark, 3090 | `07759334529ba5dcc58b6abef99391144f753ddc5c1aecbb61eb6ce79a999505` |
| dev.72 trainer, 4070 | `d298d12baebc9f6f1fea10f54d82408da1f49473ebb0898239e6697e54aec9af` |
| dev.72 checkpoint benchmark, 4070 | `1c0860443e8569cba9699d78977756450c2aaa3dcdd2ddee776a0b5daf8e5232` |

Build with `DRONEGS_BUILD_BENCHMARKS=ON`, using the retained protocol's exact
flags (same toolchain as dev.71). The output directories must be new:

```bash
BUILD/dronegs_checkpoint_topology_benchmark CHECKPOINT 1 0 0 5 NEW_REUSE_OUTPUT reuse
BUILD/dronegs_checkpoint_topology_benchmark CHECKPOINT 1 0 0 3 NEW_FRESH_OUTPUT fresh
```

## Decision and limits

Retain only the six-vector lifetime change: it reduces repeated 5M refinement
time with exact saved-state parity and stable fresh-context call timing in this
experiment. Reject both combined-allocation variants. The retained RAM and
deferred destruction costs are explicit, and small-case results are reported
without claiming a universal speedup.

This does not establish full-training speedups, browser FPS, first-view latency,
multi-scene scientific quality, cross-GPU bitwise training or behavior under
system memory pressure. Checkpoint restoration and growth preserve correctness,
but growth-allocation performance is not separately benchmarked. The retained
RAM budget must be included when sizing concurrent contexts. Viewer/server,
bundles and production images are unchanged. No artifacts were deleted.
Reverting the implementation restores per-call host vectors without migration.
