# GSTile V4 candidate-cost scratch reuse

2026-08-28. Candidate implementation; complete-build qualification pending.

## Scope and exactness

Reuse the owned left-endpoint gather for subtraction and squaring in all five
edge-distance terms. NumPy advanced indexing owns that gather; no source,
read-only input or endpoint array is modified. Keep subtraction, square and
row-wise sum/mean in their original order. Compute each vertex's squared scale
norm once, then gather the two scalar norms for the spatial denominator. The
same three-component row reduction and denominator addition are retained.

No candidate pruning, neighbour-count change, cost weight change, normalization
change, re-associated sum, precision reduction or alternative merge order.
Moment/refit calculations, proxy counts, pack workers, format, renderer and
durability are untouched. No new dependency or flag. Reverting this isolated
change restores the original expressions without a migration.

For an E-by-C difference, the old expression can retain two gathers and a
separate subtraction output; the candidate reuses one gather and then squares
it in place. This reduces that temporary's live storage, **not a total process
RSS guarantee**. Scalar scale norms add N float64 values but eliminate repeated
three-column per-edge scale gathers/squares. Full RSS is measured separately.

## Verification before timing

Two helper-contract tests fail before implementation (missing helper; stopped
at two failures). **67 new tests and 155 affected tiler/probe tests pass**.
Scoped Ruff and diff checks pass.

- Original candidate-scoring oracle frozen from `acf9e6f`, test-only.
- Exact squared deltas and owned output with C/F/strided, read-only 1D and
  1/3/15-column inputs, repeated/self edges and input immutability.
- Bit-exact costs and sorted endpoint arrays for opacity SH widths 0/3/8/15,
  neighbour counts 1/8/32, odd/even populations, shuffled large uint64 IDs,
  coincident centres/tied costs, signed zero and extreme finite values.
- Larger arrays, multi-generation proxy record/error bytes and complete bundle
  byte parity with the old scorer across workers 1/2 and individual/multi-tile
  aggregate layouts. Existing cancellation/publication tests remain green.

## Predeclared complete-build pilot

Reference: `acf9e6fd0a0f7c1245f496504651f3971a70adc6` in a retained detached
worktree. Candidate: clean implementation/driver commits recorded with results.
Same synthetic 1,048,576-record SH3/directional source (310,380,399 bytes), SHA:
`c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Local i9-13900H / 20 logical CPUs / Ubuntu WSL2, same Python interpreter and
filesystem. Python 3.12, NumPy 2.4.6, python-zstandard 0.25.0; not BIGZEN.

Both arms: adaptive V4, 16,384 proxies, 65,536 leaves, 131,072-record chunks,
2 MiB aggregate target, **one pack worker**. Two warmups, then four measured
pairs ordered AB/BA/AB/BA. Retain every trial and artifact, no cache flush or
sample exclusions. Wall time includes the complete build and durable writes;
fixture generation and output hashing are outside timing. No simultaneous
local tests/profilers or source changes during the cohort.

Acceptance is fixed before observing full timings: all files identical to the
old arm, clean pinned reports, source unchanged; at least **3% median wall-time
reduction and every paired candidate faster**. This is a practical threshold
for a smaller allocation optimization, not statistical significance. Stop and
retain failures; do not retry selectively or lower the threshold afterwards.

This is a local synthetic pilot, not a real 50 M Saint-Etienne timing claim.
At these sizes the aggregate writer has one tile per pack; multi-tile parity
is covered by the small tests, not a throughput claim from this pilot. No
repeat Chrome/GPU gate is needed if canonical output bytes are identical.
Existing PLY visual qualification is not expanded. Do not add this percentage
to the previous pair-matching or pack-worker gains.
