# Refinement telemetry — dev.67 qualification

Date: 2026-08-27. Baseline: `abcde8e85b3c23a6b00743ab8e3be4ca680ac18e`
(dev.66). This delivers audit A3 instrumentation, not GPU topology migration,
a production-training speedup or a new visual/scientific promotion. The user
accepted the preceding dev.66/GSTile lot visually; this lot changes native
diagnostics only and does not require a viewer redeployment.

## Contract and interpretation

Each native `topology_refinement` event gains a `telemetry` object; the run
manifest gains its invocation-local sum as `topology_telemetry`. The existing
event fields, outer refinement timing and lifetime refinement counters remain.
Diagnostics are deliberately not checkpointed: after resume, `measured_calls`
counts only calls in the current process, unlike restored lifetime counters.
No-refinement invocations emit zero counters/times.

The descriptor is version 1, `scope=process_invocation`,
`timing_basis=host_wall_no_extra_gpu_sync`. A nullable output pointer lets
direct callers and the benchmark disable instrumentation, including clock
reads. The trainer requests it by default. No training option, fingerprint,
selection policy, Gaussian ABI or checkpoint wire format changes.

| Phase (seconds) | Included work |
|---|---|
| `host_prepare` | Initial host-vector allocations/initialization and entry checks |
| `snapshot_download` | Gaussian AoS and five refinement-statistic arrays D2H |
| `cpu_prune` | Bounds/percentiles, pruning, survivor and recycling decisions |
| `compaction_cpu` | Host allocation, survivor gathering/packing and cleanup between transfers |
| `compaction_download` | Optimizer/refinement-state D2H during hard compaction |
| `compaction_upload` | Compacted optimizer/refinement-state and Gaussian H2D |
| `cpu_score` | Robust score normalization, candidate eligibility and seeded Gumbel keys |
| `cpu_select` | Partial selection, sorting and parent/child host indices |
| `split_upload` | Parent and destination index H2D |
| `device_submit` | Host submission of split/decay/stat-reset operations and local bookkeeping |
| `other` | Residual entry/return and host-vector destruction |

All names have the `_seconds` suffix in JSON. `total_seconds` covers the
complete public call; phase sums account for that total before serialization
rounding. Skipped phases remain zero. Serialization/aggregation is outside
the per-call total. Production adds **no CUDA events or GPU fences**.

Transfer times include existing synchronization, pageable staging and driver
costs. In particular the first download may wait for earlier GPU work.
`device_submit_seconds` is **not** GPU kernel duration. Do not derive physical
PCIe bandwidth or sum these measurements as isolated GPU execution times.

Byte counters describe requested logical payload, not physical bus traffic:

- Snapshot: `N * (296 + 5 * 4)` bytes.
- Hard compaction download: `N * 480` bytes with scalar opacity, `N * 600`
  with opacity SH; upload: survivors times `(296 + 480)` or `(296 + 600)`.
- Split upload: `added * 8` bytes. In-place recycling has no compaction traffic.

The strict optional object is defined in
[trainer-run-v1.schema.json](contracts/trainer-run-v1.schema.json). The schema
also now accepts existing scalar-opacity and dataset split-count metadata;
old manifests without the new object remain valid. `jsonschema` is a
hash-locked development-only dependency, not a production runtime dependency.

## Reproducible benchmark

Build with `DRONEGS_BUILD_BENCHMARKS=ON`, then run:

```bash
dronegs_topology_benchmark 32768 9 NEW_OUTPUT_DIRECTORY_32768
dronegs_topology_benchmark 131072 9 NEW_OUTPUT_DIRECTORY_131072
```

Directories must not exist; the benchmark retains one pre-refinement
checkpoint per case. Synthetic 128x128 reference-absolute/FastGS, SH3,
scalar-opacity cases cover no growth, hard compaction and in-place recycling.
Each case trains once, freezes that checkpoint, and restores the **same bytes**
before each instrumented/uninstrumented arm. Alternating order, one excluded
warmup pair and nine measured pairs per case. Benchmark-only fences include
actual device completion in `*_wall_seconds`; setup, checkpoint reload and
model download/comparison are outside that interval.

The benchmark checks population/decisions and all 74 float fields per Gaussian,
requiring finite values and maximum absolute delta <= `2e-6`. All 60 pairs
(including six warmups) in the v2 run were **exactly equal**, delta zero.
This is a refinement parity test, not cross-GPU bitwise training determinism.

The initial v1 experiment independently trained each arm. It failed parity
because it did not guarantee identical pre-refinement state; those results
are retained and excluded. The correction freezes the input checkpoint; the
acceptance tolerance was not relaxed.

## Measurements and limits

Local WSL Ubuntu, RTX 4070 Laptop 8,188 MiB, driver 610.62, CUDA 12.9.86,
GCC 13.3, Release/native architecture. GPU was shared with the desktop
(3,363 MiB already used before the v2 benchmark). Image
`dronegs-dev:pixel-weighted-6865308`, immutable ID
`sha256:2c74d65b960f1c867b7ac3c019666d71379e2b7c7066c5736716a9396d966edf`.

Exploratory v2 medians (milliseconds; paired delta = enabled minus disabled):

| Population / case | Disabled | Enabled | Paired delta median | Paired delta range | Median transfer-time share |
|---|---:|---:|---:|---:|---:|
| 32,768 / no growth | 12.935 | 10.784 | -1.393 | -11.016 to +8.916 | 61.1% |
| 32,768 / compaction | 51.228 | 52.010 | +3.066 | -34.051 to +33.471 | 77.0% |
| 32,768 / recycling | 10.401 | 9.524 | +0.083 | -9.771 to +6.183 | 62.0% |
| 131,072 / no growth | 41.569 | 35.462 | -2.186 | -26.002 to +24.571 | 65.7% |
| 131,072 / compaction | 176.995 | 177.586 | -1.387 | -37.716 to +32.978 | 70.3% |
| 131,072 / recycling | 43.742 | 40.338 | -7.338 | -23.975 to +20.197 | 62.9% |

Negative differences are noise, **not an instrumentation speedup**. These
results do not establish a precise overhead bound. Instrumentation adds only
host fields/clock reads and no device allocations; peak whole-process VRAM
has not been separately profiled. End-to-end logging overhead is not isolated.

Transfer-bound phases dominate these synthetic cases, and hard compaction adds
large round trips. This supports testing stable GPU survivor compaction before
GPU top-K, but does not establish the bottleneck on Saint-Etienne, million-splat
training or another GPU. Next gates: capture native corpus phase shares,
preserve source order/Adam mapping, then compare GPU compaction with the frozen
checkpoint protocol and the existing production quality gates. Hot/cold or SoA
layout migration remains separate work.

## Retained evidence and checks

All evidence lives under `/home/olivier/droneai-qualifications/`:

- `refinement-telemetry-20260827-v1`: initial build, eight passing CTest suites
  and the invalid independent-training benchmark failure (retained).
- `refinement-telemetry-20260827-v2`: fixed benchmark checkpoints, JSONL,
  build/configure/CTest logs and binaries. Eight native suites pass, including
  the 400-step trainer aggregate test. Native binary SHA-256
  `f8df063d92dd7ed6601eedfab9fa13ede50c024ad4b29b62e123360a5039c088`;
  benchmark `82d8a6f93b7513a412c048dfecb33edfffcef224d290a1bc4f54be4fe1bd102c`.
  These exploratory binaries precede the dev.67 version-string update.

CPU tests cover zero initialization, aggregation, JSON serialization and
manifest wiring. CUDA tests cover reset, no-growth/recycling/compaction bytes,
opacity SH both off/on, phase accounting and checkpoint/resume. Twelve schema
tests cover optional diagnostics, current appearance modes, invalid/missing
counters and misleading timing descriptors. The new native core tests use the
existing required CPU CI target; hardware qualification runs separately.

No production long-training, portable multi-architecture performance, browser
or new visual gate is claimed for this instrumentation-only change.
