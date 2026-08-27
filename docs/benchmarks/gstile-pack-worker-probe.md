# GSTile bounded pack-worker probe

2026-08-27. Completed experimental microbenchmark, not a production tiler change.

## Predeclared question

Does independent pack encoding/compression scale with 1, 2 and 4 Python
threads while keeping canonical bytes, Zstd output, quantization metadata and
error bounds identical? This addresses audit item 7 before modifying traversal,
aggregate packing or durability.

The prototype submits at most one job per worker, consumes results in source
order and cancels pending work on failure. Already-running jobs finish before
shutdown. Every compression job owns its Zstd context; settings match production
level 1, checksum and content size. Inputs are read-only. No production writer,
fsync, atomic rename, partitioning, proxy generation or scheduler is modified.

## Protocol

Tool: `tools/benchmark_gstile_pack_workers.py`. Three real Saint-Etienne fixtures
(proxy, leaf, large leaf) are verified against their existing index and CRC.
Their bundle is `sha256:90866aeeedfb09192fcb94bd9a02cf2b466b5a9345b3785d51d9ae0f98ee87b3`.
Separate stages:

1. Real-pack Zstd + hashes, cycling those three fixtures.
2. Encode + Zstd + hashes of a seeded synthetic SH3/directional input, 65,536
   records including IDs above 2^53. This is not a spatially partitioned real PLY.

Each trial has 48 jobs. One warmup per worker count, then three measured orders:
1/2/4, 4/2/1, 2/1/4. All observations are retained, no best-of selection.
Thread creation/shutdown is included. Inputs are hot in memory; disk throughput
and durability are outside the measurement. Timings include hash checks present
in the production write path, plus result bookkeeping. Parallel signatures and
metadata must exactly match the sequential warmup. Focused tests also compare
the actual raw/compressed bytes and metadata, not only hashes.

The output directory is exclusive-create. Protocol, versions, source/fixture
hashes, every warmup/trial, output signatures and summary are retained. A failure
keeps earlier observations. RSS is explicitly the cumulative process high-water
mark, not an independently attributed per-arm peak. The task count and record
count are bounded; no unbounded future queue is permitted.

```sh
.venv/bin/python tools/benchmark_gstile_pack_workers.py \
  /home/olivier/droneai-qualifications/gstile-fused-decode-20260827/fixtures \
  /home/olivier/droneai-qualifications/gstile-pack-workers-20260827
```

Eight focused tests pass: exact outputs at all worker counts, bounded lazy
submission, propagation of failure without scheduling the remaining input,
and rejection of invalid worker counts. Scoped Ruff and diff checks pass.
The full affected tiler suite plus these tests passes: **41 tests**.

## Result and decision

Measured on local Ubuntu WSL2, Intel Core i9-13900H, 20 logical CPUs exposed,
not BIGZEN. Source commit `6d855986d0c33c8c2f06620088c940b937739292`, clean tree;
Python/NumPy/Zstd versions and fixture hashes are retained in `protocol.json`.

| Stage, 48 jobs | 1 worker median | 2 workers median | 4 workers median | 4-worker factor |
| --- | ---: | ---: | ---: | ---: |
| Real packs: Zstd + hashes | 0.5719 s | 0.3426 s | 0.1949 s | 2.93× |
| Synthetic: encode + Zstd + hashes | 4.8249 s | 2.8673 s | 1.7698 s | 2.73× |

All 24 trials, including six warmups, have matching ordered output signatures
and quantization/error metadata within each stage. The shared input is unchanged.
The input is hot and repeated; the encode population is synthetic. The 1-worker
control also uses the executor, not the production inline writer. These are
local codec throughput factors, **not a complete tiler acceleration**. More CPU
service is consumed by four workers despite the shorter wall time. The largest
cumulative RSS counter is 268,232 KiB; it cannot be compared as per-arm RSS.

Evidence directory:
`/home/olivier/droneai-qualifications/gstile-pack-workers-20260827`.
It retains the frozen protocol, all raw timing rows, ordered output signatures
and summary. Original real packs remain in the source fixture directory.

Decision: proceed to a separate production integration experiment. First split
pure pack preparation (CRC/hash/Zstd) from the ordered atomic writer, then use
bounded futures with an explicit byte ceiling. `pack_tile` and `_flush_aggregate`
currently need synchronous metadata: simply putting their existing calls in
an executor and immediately waiting would not expose useful parallel work.
Deferred completion must preserve tile references, pack order, aggregate IDs,
progress ordering, exception handling and final publication after all writes.
Keep serial mode as the control; compare complete canonical bundles and sidecars
before enabling it. No fsync relaxation or parallel V4 is justified by this probe.
