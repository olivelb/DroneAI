# GSTile V4 blocked candidate distances

2026-08-28. **Full-build pilot accepted: 5.32% median time reduction, exact files.**

## Hypothesis and unchanged contract

Compute the four multi-column squared edge distances in blocks of 4,096
edges. Reuse each owned left gather for subtraction and square, then perform
the original row-wise sum or mean. Precompute the three-column scale norm
once per vertex, retaining the same row reduction and endpoint addition.
Scalar opacity and the final cost expression are unchanged. Every robust
normalization still uses the complete edge vector, never a block median.

No pruning, altered neighbours, weights, precision, candidate sorting, proxy
moment/refit, workers, renderer, format or durability changes. The private
helper receives float64 attributes and returns a complete float64 edge vector.
Only its matrix scratch is bounded: two 4,096-by-15 float64 gathers use at most
0.94 MiB. This is not a bound on all arrays or process RSS. Partial and empty
edge blocks, read-only/strided inputs and exact byte parity are tested.

Before timing: **80 cost/helper tests and 168 affected tiler/probe tests pass**,
with scoped Ruff and diff checks. The original scoring expression remains a
frozen test oracle. It covers widths 0/3/8/15, neighbours 1/8/32, extreme finite
values, tied/signed-zero costs, large source IDs, full proxy/error bytes and
complete bundle parity. New helper contracts cover C/F/strided layouts,
sum/mean, empty through multi-block tails, immutable inputs and scratch bounds.

An exploratory 65,536-record cost-only probe found a 0.4781 to 0.3701 second
median reduction (22.6%) with 4,096-edge blocks. It tested blocks 4,096,
16,384 and 65,536, retained all trials and verified identical costs/order.
This motivates the fixed block size; it is not an independent full-build
speedup or proof of a universally optimal size. Evidence remains in
`/home/olivier/droneai-qualifications/gstile-blocked-cost-probe-20260828`.
The earlier unblocked scratch attempt failed its full-build gate and remains
rejected; this is a new experiment, not a reinterpretation of its results.

## Predeclared full-build protocol

Reference: clean `aa095acfd477e8d9875ae286424c542b766ac3ea`, production
identical to `acf9e6f`, in a retained detached worktree. Candidate runtime and
driver commits are pinned before timing. Same synthetic 1,048,576-record
SH3/directional source (310,380,399 bytes), SHA-256
`c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Local i9-13900H / 20 logical CPUs / Ubuntu WSL2; Python 3.12, NumPy 2.4.6,
python-zstandard 0.25.0; not BIGZEN. Same interpreter/filesystem for both arms.

V4 adaptive, leaf 65,536, chunks 131,072, proxies 16,384, aggregate target
2 MiB, **one pack worker** in both arms. Two warmups, then four measured pairs
AB/BA/AB/BA. No simultaneous local tests/profilers or source edits, no cache
flush and no excluded samples. Fresh process per build; whole-build wall time
includes durable writes, excludes fixture generation and output hashing.

Acceptance fixed before full timings: **at least 3% median wall-time reduction
and every measured pair faster**, all bundle files identical, clean pinned
reports, unchanged source hash. Median is the average of the two central
values. This practical threshold is not statistical significance. Retain and
stop on failures; do not selectively retry or lower the threshold. If it
fails, restore production and retain the negative result and evidence.

This pilot is synthetic, not the real 50 M Saint-Etienne dataset. Its packs
hold one tile each; separate small tests cover multi-tile aggregation and
workers 1/2. Exact bundle bytes mean no additional Chrome/GPU visual gate for
this isolated producer change; no expansion of prior PLY visual claims. Do
not sum its percentage with previous pair-matching or worker gains.

Evidence root (all outputs, raw reports, inventories, worktree retained):
`/home/olivier/droneai-qualifications/gstile-v4-blocked-costs-20260828`.
Source retained at
`/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply`.

## Results and decision

All **10 builds completed** without failed or excluded samples. All 630 files
(manifest, raw packs and Zstd sidecars) match the reference inventory. A second
check using explicit-argv recursive `diff -rq` confirms that all nine other
bundle directories are binary-identical to the first reference. All raw reports
are clean and pinned; the source checksum remains unchanged. Common bundle ID:
`sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

| Trial | Order | Reference seconds | Blocked seconds | Time reduction |
| --- | --- | ---: | ---: | ---: |
| Warmup | AB | 29.352 | 28.011 | — |
| Pair 1 | AB | 30.097 | 27.459 | 8.77% |
| Pair 2 | BA | 29.361 | 27.901 | 4.97% |
| Pair 3 | AB | 29.627 | 28.416 | 4.09% |
| Pair 4 | BA | 29.855 | 28.488 | 4.58% |
| Measured median | — | **29.741** | **28.158** | **5.32%** |

The fixed 3% median threshold and every-pair-faster gate pass. The warmups are
retained but excluded from the median exactly as declared. Peak child RSS is
464,080 KiB reference versus 465,308 KiB blocked; **no total RSS reduction is
demonstrated**. Filesystem output blocks are 3,381,408 for every build. The
full-build gain is smaller than the exploratory cost-only microbenchmark and
does not establish a speedup for other datasets, machines or configurations.

**Decision:** retain the blocked implementation and the numerical contracts.
No runtime flag, public parameter, bundle migration or renderer change is
needed. Rollback is restoration of the old scorer from `aa095ac`; existing
bundles remain usable either way. The previous unblocked experiment stays
rejected on its own evidence. Next investigation should reprofile remaining
proxy moment/refit work instead of assuming the original bottleneck persists.

## Provenance and reproduction

- Runtime: `922269da1b037c5ff28d439b2c5a354386d036a9`.
- Measured clean candidate with frozen driver:
  `51dce6f01281d392c0f16008a5e206de1f7df8bf`.
- Reference: `aa095acfd477e8d9875ae286424c542b766ac3ea`.
- Versioned [driver](gstile-v4-blocked-costs-builds.mjs) and
  [results](gstile-v4-blocked-costs-results.json): protocol, all ten trials,
  paired reductions, complete control inventory and raw report hashes.
- Evidence root above retains approximately 1.4 GiB: all bundles, reports,
  stdout/stderr, inventories, protocol/trials, independent binary-check script
  and result, and the detached reference worktree. No cleanup requested/done.

From the measured candidate (or a clean tree with identical producer code),
create a **new** evidence directory and a detached reference worktree named
`baseline` at the exact reference commit. With the original source in its
recorded path, run:

```sh
node docs/benchmarks/gstile-v4-blocked-costs-builds.mjs /absolute/new-evidence-directory
```

Existing results directories are refused. Runtime and source checksums are
checked before running; each report's commit/clean state and all files are
checked, and source hash is rechecked afterwards. Any altered fixture, block
size, worker count or runtime needs a separately declared protocol.
