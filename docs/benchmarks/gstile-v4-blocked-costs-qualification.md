# GSTile V4 blocked candidate distances

2026-08-28. Implementation and protocol; full-build qualification pending.

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
