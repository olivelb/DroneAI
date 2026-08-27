# Dev.71 bounded CPU pruning percentiles

Date: 2026-08-27. Audit A4, following the
[dev.70 compact pruning snapshot](PRUNING_SNAPSHOT_DEV70.md).
This is a native refinement scheduling change, not a new scientific profile,
renderer, checkpoint format or production image deployment.

## Design and exactness contract

Pruning needs q10/q90 for each spatial axis and p80 of maximum scale. These
four computations are independent. Each still uses the existing exact
`std::nth_element` floor-index helper with the same input bits and order.
The caller calculates scale p80 while up to three tasks calculate axis pairs.
Bounds, scale limits, pruning, survivor order, fallback, scoring, Gumbel/top-K,
splitting and decay retain their existing equations and application order.
No approximate quantile or GPU selection is introduced.

The policy enables tasks only at 262,144 or more scale values, on a machine
reporting at least four hardware threads. An individual axis below that size
stays sequential, even if other axes qualify. Zero/unknown hardware concurrency
and small populations take the sequential path. The bound is three additional
threads **per refinement call**, not a global process-wide worker quota.
The caller remains the only thread using CUDA. Scale values retain their
source order for the subsequent pruning pass.

Each task accesses a separate vector. Futures are destroyed before their
captured coordinates, including during exception unwinding. The
[`std::async` launch and synchronization contract](https://eel.is/c++draft/futures.async)
was checked: `async | deferred` lets the runtime select deferred execution,
including when an asynchronous thread is unavailable. This does not swallow
allocation failures or promise recovery from every resource failure. No
external implementation was copied.

`hardware_concurrency()` is only a hint: it does not provide a hard affinity,
container CPU quota or concurrent-trainer budget. Oversubscribed/constrained
hosts and forced thread-exhaustion fallback were not qualified. The fixed
threshold is conservative on the two measured CPUs, not globally optimal.
Small fixtures are sequential controls, not expected speedups.

There are no new GPU allocations or transfers. Running scale selection while
axis scratch vectors remain live can increase transient host overlap by up to
one N-float copy (20 MB at 5M), plus task state and thread stacks. This is not a
host-memory optimization. `cpu_prune_seconds` includes task scheduling and
joining as elapsed wall time, not summed worker CPU time; telemetry schema
and byte formulas remain unchanged.

## Protocol and provenance

Candidate source, clean when configured:
`2da4aafb656f8bf33cddf55dacdf661ea10aa4e2`.
Reference: retained dev.70 binary from
`a7af065e0b1bda064e3e9d8ef70f5faa18d55392`, whose native code matches
pre-change main `3b2a3694fe87ef91c7b47b6a62a6078ccb412b8f`.
Follow-up documentation does not change the qualified native code.

The real input and checkpoint benchmark are unchanged from dev.70:

```text
/home/olivier/droneai-door-retraining-saint-etienne-1mm/checkpoints/porte-strict-saint-etienne-1mm/full/training.ckpt
```

5,000,000 Gaussians, reference-absolute/FastGS, SH3, opacity SH **on**, seed 42,
30,000 completed steps, 4,540,000,774 bytes. SHA-256:
`2f33e7572dc749fbc07cce3ade9390cd9f25729e73d7a4751aa250588c8a26c9`.
The explicit mode preserves this historical fixture; Production V1's scalar
opacity default is unchanged. This is a manual prune-only diagnostic on final
state, not a scheduled training iteration. Reset statistics give zero growth
candidates, 120,986 pruned Gaussians and 4,879,014 survivors.

Each repetition reloads the frozen input. Context setup, checksum/load and
output saving are outside the interval; a benchmark-only fence includes GPU
completion. One excluded warmup per group saves the full resulting checkpoint.
Order: dev.70 A, five measured calls; dev.71, five; dev.70 B, three. These are
sequential groups, not randomized pairs. The same retained 32,768-Gaussian
scalar-opacity fixtures cover no growth, compaction/split and recycling, with
one excluded warmup and five measured calls per arm.

All new output checkpoints are saved on BIGZEN's local I: mount, not its nearly
full WSL disk. Both arms use the same output filesystem; timed work excludes
saving, comparison and hashing. Old inputs/outputs remain intact. This storage
change cannot be used to infer checkpoint-I/O or end-to-end speedups.

## Real refinement and full-state parity

| Group | Median fenced refinement (s) | Min–max (s) |
|---|---:|---:|
| dev.70 reference A | 0.468442 | 0.463557–0.480436 |
| dev.71 candidate | 0.317923 | 0.305492–0.327293 |
| dev.70 reference B, after candidate | 0.461041 | 0.452871–0.463909 |

The isolated refinement is **32.13% shorter (1.47x)** than reference A and
31.04% shorter than reference B. The observed ranges do not overlap. This is
incremental over dev.70, not a
whole-training or browser-rendering speedup.

| Phase median / payload | dev.70 A | dev.71 |
|---|---:|---:|
| Host preparation (s) | 0.063706 | 0.060984 |
| Snapshot projection/download (s) | 0.029225 | 0.030580 |
| CPU pruning, including selection (s) | 0.279418 | 0.130588 |
| CPU compaction (s) | 0.016224 | 0.015675 |
| Compaction upload (s) | 0.001924 | 0.001995 |
| CPU scoring (s) | 0.025736 | 0.018367 |
| Device submission (s, not GPU elapsed) | 0.007632 | 0.007753 |
| Other / host destruction (s) | 0.012781 | 0.010571 |
| Public call, before benchmark fence (s) | 0.435535 | 0.275269 |
| Snapshot download bytes | 260,000,000 | 260,000,000 |
| Compaction upload bytes | 19,516,056 | 19,516,056 |

CPU pruning wall time falls 53.26%. Phase medians need not sum to the median
total. Scoring math is unchanged and its timing variation was not independently
profiled. The candidate public call still returns before the device finishes:
roughly 43 ms remain in this fixture. The speed claim uses **0.318 s including
the fence**, not the 0.275 s unfenced call. No production fence was added.

Warm-call `cudaMemGetInfo` deltas remain zero; each arm's first call uses the
same extra 2 MiB of driver/runtime memory. Whole-benchmark peak RSS is
4,440,292 KiB for A, 4,455,752 KiB for the candidate (+15,460 KiB), and
4,440,808 KiB for B. This peak
includes checkpoint load/save, not just refinement; no isolated-refinement
memory peak was instrumented. The extra transient CPU overlap is an explicit
tradeoff, not a claimed memory reduction.

All nine saved output checkpoints are byte-identical to their corresponding
retained dev.70 outputs, including parameters, moments, statistics and progress.
Each of the three real outputs is 4,430,145,486 bytes, SHA-256:
`743e10f97750b8bc8014a6541f65aeee9cc8dd2f60b1e79c4600aac466f987fe`.
The original input checksum is unchanged after qualification.

| Synthetic path | Pruned / added | dev.70 median (ms) | dev.71 median (ms) | Full checkpoint |
|---|---:|---:|---:|---|
| No growth | 0 / 0 | 2.409 | 2.292 | Exact |
| Hard compaction + split | 2,048 / 18,432 | 4.664 | 4.424 | Exact |
| In-place recycling | 128 / 128 | 2.772 | 2.684 | Exact |

These 32,768-input fixtures stay below the parallel threshold. The ranges
overlap in all three cases: A/candidate ranges are 2.115–2.629/2.097–2.457 ms,
4.325–4.775/3.852–5.450 ms and 2.451–2.874/2.580–2.794 ms respectively.
No synthetic speedup is attributed to this sequential control. Both arms
match the previous lot's full-state hashes:

- No growth: `6a6f95332ed4f4c74c4632e3658b84df7e720d9e0dd268ac3a25b735bf50a9cf`.
- Compaction: `519d002403701bc33f1ff492bde693f7a9a70e03a7403d4b76029b66e2cf62bb`.
- Recycling: `0d222aa2ee260c02838e8665069e89204748121c6cf0d9ba6db1ee594055c08b`.

## Isolated CPU selection experiment

The optional `dronegs_topology_percentile_benchmark COUNT REPEATS` target
compares serial exact helpers with the new bounded helper on identical seeded
finite vectors. Seed 42; three LCG coordinate streams; scale is x times 0.001.
Inputs are copied outside the timer. Allocation/copy of scale scratch, axis
selection/destruction, scheduling and joining are inside it. Arm order
alternates, with one excluded warmup pair and nine measured pairs per size.
Every pair verifies all seven returned float values bitwise.

All times below are milliseconds. Paired delta is bounded minus serial; its
median is not necessarily the difference between the two arm medians.

| CPU | Values | Serial median | Bounded median | Paired delta median [min, max] |
|---|---:|---:|---:|---:|
| i9-13900H | 32,768 | 1.046 | 1.033 | +0.006 [-0.186, +0.037] |
| i9-13900H | 131,072 | 4.112 | 4.111 | -0.028 [-0.474, +0.162] |
| i9-13900H | 262,144 | 7.844 | 2.950 | -4.838 [-5.351, -4.568] |
| i9-13900H | 1,048,576 | 38.691 | 12.223 | -26.307 [-28.102, -25.413] |
| i9-13900H | 5,000,000 | 175.059 | 57.830 | -114.935 [-125.633, -107.200] |
| Ryzen 9 5950X | 32,768 | 1.078 | 1.088 | -0.004 [-0.136, +0.307] |
| Ryzen 9 5950X | 131,072 | 4.439 | 4.533 | +0.253 [-0.866, +0.388] |
| Ryzen 9 5950X | 262,144 | 8.264 | 3.559 | -4.681 [-5.316, -3.934] |
| Ryzen 9 5950X | 1,048,576 | 41.569 | 14.434 | -27.417 [-30.007, -24.881] |
| Ryzen 9 5950X | 5,000,000 | 178.833 | 59.117 | -120.032 [-124.261, -103.963] |

At 5M, isolated selection wall time falls about 67% on both CPUs. At the
262,144-value threshold it falls 62.39% on the i9 and 56.94% on the Ryzen.
All 100 pairs, including ten warmups, retain bitwise equality. This is a CPU
microbenchmark, not an additional full-training or whole-refinement speedup.

Below-threshold differences are noise on the sequential control; this
experiment does not establish a sub-percent overhead bound. Preliminary local
measurements preceded version metadata finalization and are retained separately;
the results above use the exact committed CMake target.

## Hardware and verification

BIGZEN: WSL Ubuntu 24.04, Ryzen 9 5950X (32 reported logical CPUs), 94 GiB RAM,
RTX 3090 24,576 MiB, driver 591.74, about 675 MiB desktop GPU use before the run.
CUDA 12.0.140, CUDA host GCC 12, C++ GCC 13.3, CMake 3.28.3, Ninja 1.11.1,
Python 3.12.3, Release/native sm_86. Same extracted JPEG headers as dev.70;
no system packages installed. No concurrent training was started.

Local: WSL Ubuntu, i9-13900H (20 reported logical CPUs), RTX 4070 Laptop
8,188 MiB, driver 610.62, CUDA 12.9.86, GCC 13.3, Release/native sm_89.
Existing Docker image `dronegs-dev:pixel-weighted-6865308`, immutable ID
`sha256:2c74d65b960f1c867b7ac3c019666d71379e2b7c7066c5736716a9396d966edf`.
Hosts remain shared with the desktop; CPU microbenchmarks run without another
qualification workload started on the same host.

- 60 new CPU cases compare seven result floats bitwise with the legacy helpers
  and coordinate ranks numerically with an independent sorted oracle. They
  verify unchanged scale input bits/order, empty/small inputs, threshold minus
  one/exact/plus one, ascending/descending data, duplicates, signed zeros,
  constants and axes with unequal sizes (including empty).
- CPU tests pass standalone with strict GCC warnings and under ThreadSanitizer
  and AddressSanitizer/UndefinedBehaviorSanitizer, with no reported errors.
- All eight native suites pass on both GPU/runtime pairs (local 3.55 s, BIGZEN
  2.57 s), including existing bounded/FastGS compaction/split/resume coverage
  with opacity SH off/on and the 400-step trainer aggregate test.
- Compute Sanitizer memcheck reports zero errors on the exact local training
  test binary. All 22 targeted Python contracts and repository static checks pass.
- Portable CUDA library compilation passes sm_75/80/86/87/89/90/100/101/120.
  Only sm_86 and sm_89 were executed; only sm_86 has real-checkpoint timings.
- All 52 real/synthetic telemetry objects, including warmups, pass JSON Schema,
  phase accounting and the unchanged snapshot/compaction/split byte formulas.

The core cases use the existing required CPU CI target. GPU execution evidence
is manual hardware qualification, not a claim that a disabled GPU CI job ran.
The finite-input tests do not redefine the existing helper's NaN ordering
policy. No acceptance threshold or scientific policy was relaxed.

## Reproduction and retained evidence

BIGZEN source, build, protocol, JSONL, resource logs and hashes:
`/home/olivier/droneai-qualifications/parallel-percentiles-20260827`.
Its nine output checkpoints:
`/mnt/i/DroneAI-Qualifications/parallel-percentiles-20260827`.
Reference binary/outputs remain under `pruning-snapshot-20260827`; synthetic
inputs and JPEG headers remain under `gpu-compaction-20260827`.

Local source archive, native/portable builds, CPU microbenchmarks, sanitizer
binaries/logs, mirrored BIGZEN logs, analysis and scripts:
`/home/olivier/droneai-qualifications/parallel-percentiles-20260827-exact`.
Preliminary tests/CPU experiments and transport logs remain in the sibling
`parallel-percentiles-20260827-green`, `-cpu` and `-transfer` directories.

Source archive SHA-256:
`592e54b6cfd536d3d3fb979240bb5aaeef719d0e8550bf613fd6cbf5e95f0e2f`.

| Binary | SHA-256 |
|---|---|
| dev.70 checkpoint benchmark, 3090 | `254c19789fff99559e55bce3ec0d500aaf48f90f516c64a6281c28fc5ac60d0c` |
| dev.71 trainer, 3090 | `1486947abca4ddb44c42aab61658c481534b67ec99bb43e59c76fec613a4d0a2` |
| dev.71 checkpoint benchmark, 3090 | `452ebf92d398d5fc03227c34f814d144caa2086092d3a665f9fe5df1f6704b91` |
| dev.71 trainer, 4070 | `d56f7f827377439d9101d68174f62025a1968e1a2041cd41f80c4932adb7e56e` |
| dev.71 checkpoint benchmark, 4070 | `9c111f61697fa7bb742e1336ae32293eae477566c40426d933bf25b8d8a98355` |
| dev.71 CPU benchmark, both hosts | `e1e12c751b2035c28a0058f53a45e44b2887c91645f449d7dca1659a1e73d3f0` |

Build as in the [dev.70 reproduction commands](PRUNING_SNAPSHOT_DEV70.md), with
`DRONEGS_BUILD_BENCHMARKS=ON`. The retained `protocol.sh` has the exact paths,
build flags, group order and comparisons. Example benchmark invocations:

```bash
BUILD/dronegs_topology_percentile_benchmark 5000000 9
/usr/bin/time -v BUILD/dronegs_checkpoint_topology_benchmark \
  CHECKPOINT 1 0 0 5 NEW_OUTPUT_DIRECTORY
cmp REFERENCE/after-refinement.ckpt CANDIDATE/after-refinement.ckpt
```

No artifacts were deleted. Original input, viewer bundles/server and production
images are unchanged. Reverting this implementation restores sequential CPU
selection without any persisted-state migration.

## Decision and remaining limits

Keep the bounded implementation: the measured 5M CPU and fenced-refinement
gains justify it with exact frozen-state parity. Keep small populations
sequential; no new user-facing tuning option or unbounded worker pool is added.

No full-training, browser-FPS, first-view, cross-GPU bitwise training, portable
architecture performance or new scientific quality promotion is claimed. Exact
one-refinement state parity plus synthetic growth/resume tests does not replace
long multi-scene, multi-seed A/B. Device-side topology and hot/cold layout remain
separate larger experiments; the CPU per-Gaussian pass and scoring/top-K are
still available for profiling-driven optimization.
