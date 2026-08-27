# GSTile V4 candidate-cost scratch reuse

2026-08-28. **Performance gate failed; experimental runtime not delivered.**
Only the numerical regression tests and this retained evaluation are delivered.

## Evaluated change and exactness

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

## Experimental verification before timing

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

## Results: insufficient gain, rejected

All **10 builds completed**, with no excluded sample or runtime failure.
All 630 files, including raw packs, Zstd sidecars and manifest, are identical
to the reference. The source hash is unchanged and every report is clean and
pinned. An independent explicit-argv recursive binary comparison confirms the
other nine bundle directories match the reference. The common bundle ID is
`sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

| Trial | Order | Reference seconds | Scratch seconds | Time reduction |
| --- | --- | ---: | ---: | ---: |
| Warmup | AB | 28.485 | 28.691 | — |
| Pair 1 | AB | 30.004 | 28.782 | 4.07% |
| Pair 2 | BA | 30.441 | 29.448 | 3.26% |
| Pair 3 | AB | 30.115 | 29.697 | 1.39% |
| Pair 4 | BA | 30.133 | 29.931 | 0.67% |
| Measured median | — | **30.124** | **29.572** | **1.83%** |

All measured pairs are slightly faster, but the fixed **3% median gate fails**.
The warmup is not silently discarded from evidence: it is slower with scratch
reuse and excluded from medians only as predeclared. The median averages the
two central observations. Peak child RSS is 462,412 KiB reference versus
464,760 KiB scratch: no process-memory reduction was observed. Filesystem
output blocks remain 3,381,408 in every run.

**Decision:** restore production `tiler.py` byte-for-byte to `acf9e6f`; do not
ship scratch reuse or norm precomputation. Preserve the experimental commits,
all raw evidence, driver and results. Retain 55 algorithm-independent numerical
contract tests for the next attempt; the 12 tests specific to the discarded
helper remain in the experimental commit, not in the delivered suite. The
delivered baseline suite has 143 affected tests. No dead production flag or
fallback path is added. Existing renderer and bundles are unchanged.

This does not establish a meaningful general speedup or a regression outside
the tested pilot. A future blocked-distance experiment would need a fresh
protocol, exact costs/ordering and complete-build evidence; it must not be
presented as already effective. Do not reduce the threshold after seeing data.

## Provenance and reproduction

- Runtime commit: `3888e2a8e0d24a1dc928f3da785b07ac2b7bb8c2`.
- Clean measured candidate with frozen driver:
  `6ae2d70b1b86f398ee91413e250c1adf0bb7e209`.
- Retained evidence:
  `/home/olivier/droneai-qualifications/gstile-v4-costs-20260828`.
  Reference worktree, all bundles, raw reports/stdout/stderr, inventories,
  protocol, trials, verified results and independent binary check are retained.
- Original source retained at
  `/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply`.
- Versioned [driver](gstile-v4-costs-builds.mjs) and
  [results](gstile-v4-costs-results.json) include pinned provenance, every trial,
  raw report hashes and the complete reference file inventory.

Run the driver from the retained **experimental candidate commit** above (not
the final delivery, which restores the baseline), with a new evidence directory and a
detached reference checkout named `baseline`. The fixture path/hash, code pins
and configuration are deliberately fixed; declare a new protocol before
altering them. Existing results directories are refused. The source hash is
checked again at the end; post-build hashing is outside the timed region.
The diagnostic from `gstile-v4-pairs-profile-20260828` motivated this change
but is not a baseline for the performance calculation.
