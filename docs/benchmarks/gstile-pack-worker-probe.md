# GSTile bounded pack-worker probe

2026-08-27. Experimental microbenchmark, not a production tiler change.

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
Results are pending. A positive microbenchmark is only permission to attempt
integration with byte backpressure and an ordered atomic writer; it is not a
full-tiler speedup or a reason to parallelize V4 automatically.
