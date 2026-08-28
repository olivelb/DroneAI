# GSTile directional-opacity refit scratch reuse

2026-08-28. **Rejected: full-build median is 0.49% slower; runtime not delivered.**
Only reusable numerical contracts and the retained evaluation are delivered.

## Hypothesis and unchanged computation

The refit owns its sigmoid output. Weight that array in place before the same
group reduction, then reuse the reduced matrix for the original divide,
clip, odds division and log operations. Keep coefficient stacking, sigmoid,
direction sampling, both matrix products, pseudoinverse, group ordering,
ellipsoid areas, floors and clipping thresholds unchanged. No algebraic
rewrite, precision reduction, new dependency, worker change or cache.

The reduced buffer progresses from directional mass to alpha to fitted target
logits. Inputs remain immutable. The subtraction `1 - alpha` still creates a
temporary; this is a reduction in intermediates, not a constant-memory refit
or RSS guarantee. Final fitted-finiteness checking and failure text are unchanged.
No proxy membership, format, renderer, durability or publication change.

## Exploratory probe and exactness

Clean reference `e651905f593cafe293d74d4d1ef8251e2cedd442`. Separate cost-only
probe: 65,536 synthetic SH3 records, group counts 32,768 and 4,097; one warmup
per arm/group and four AB/BA pairs, all 20 trials retained. Fitted float64
bytes match in every run. Medians:

| Groups | Original seconds | Scratch seconds | Reduction |
| ---: | ---: | ---: | ---: |
| 32,768 | 0.072915 | 0.070868 | 2.81% |
| 4,097 | 0.063798 | 0.055345 | 13.25% |

These modest isolated gains, particularly for V4 pair groups, are **not a
complete-build speedup**. Probe fixture SHA-256:
`4892c12860228cb97128aa5850d9f99a66fe579792f6031f1a0ff9edef90257d`.
Script retained at
`/home/olivier/droneai-qualifications/gstile-refit-scratch-probe-20260828.py`,
protocol/trials/summary in the sibling directory without `.py`.

The original refit expression is frozen as a test-only oracle. Its 69 tests
pass on the baseline before modification. Tests cover exact fitted float64
bytes for opacity SH widths 0/3/8/15, normal/saturated/signed-zero coefficients,
normal/floor/clipped/extreme areas, singleton through whole-population groups,
large matrices, strided/read-only inputs and unchanged nonfinite failures.
Multi-generation record/error bytes and complete bundles across workers 1/2
and individual/multi-tile aggregate layouts are compared. Unchanged sigmoid,
design, area and pseudoinverse helpers remain shared with the oracle; the
full-build comparison uses separate pinned checkouts.
After implementation, **69 refit contracts and 364 affected tiler/probe tests
pass**, together with scoped Ruff, Markdown link and diff checks.

## Predeclared complete-build protocol

Reference: retained detached `e651905f593cafe293d74d4d1ef8251e2cedd442`.
Pin clean implementation and driver commits before timing. Use the same
synthetic 1,048,576-record SH3/directional source, 310,380,399 bytes:
`/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply`,
SHA-256 `c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Local i9-13900H / 20 logical CPUs / Ubuntu WSL2, Python 3.12.3, NumPy 2.4.6,
python-zstandard 0.25.0, same interpreter/filesystem in both arms. Not BIGZEN.

Both arms: V4 adaptive, 65,536 leaves, 131,072-record chunks, 16,384 proxies,
2 MiB aggregate target, **one pack worker**. Two warmups then four measured
pairs AB/BA/AB/BA. Fresh process per build; whole-build wall time includes
durable writes, excludes generation/hashing. No concurrent local tests or
profilers, no source edits during the cohort, cache flush, excluded samples
or selective retry. Preserve every output and failure.

Acceptance fixed before timing: **at least 3% median wall-time reduction and
every measured pair faster**, all bundle files identical, clean pinned reports,
unchanged source checksum. Median averages the central two observations. This
is a practical gate, not statistical significance. If it fails, restore the
production code; do not lower the threshold or justify delivery by post-hoc
sample selection. Keep the negative result and reusable numeric contracts.

This is a synthetic V4 pilot, not the real Saint-Etienne 50 M dataset. At its
sizes each aggregate contains one tile; multi-tile correctness is tested
separately, not a throughput claim here. With identical canonical bytes and
unchanged renderer, no repeat Chrome/GPU visual gate is needed. No expansion
of the prior PLY visual qualification, no addition of previous gains.

Evidence root, retained without cleanup:
`/home/olivier/droneai-qualifications/gstile-refit-scratch-20260828`.

## Result: no qualified speedup

All ten builds completed, with no failed or excluded sample. All 630 files
(manifest, raw packs and Zstd sidecars) match the reference inventory. An
independent explicit-argv recursive `diff -rq` confirms all nine other bundle
directories are binary-identical to the first reference. Source checksum
unchanged and all reports clean/pinned. Common bundle ID:
`sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

| Trial | Order | Reference seconds | Scratch seconds | Time reduction |
| --- | --- | ---: | ---: | ---: |
| Warmup | AB | 21.235 | 23.493 | — |
| Pair 1 | AB | 23.646 | 21.862 | 7.55% |
| Pair 2 | BA | 21.884 | 21.620 | 1.20% |
| Pair 3 | AB | 21.116 | 21.590 | −2.24% |
| Pair 4 | BA | 20.476 | 21.591 | −5.45% |
| Measured median | — | **21.500** | **21.606** | **−0.49%** |

Both fixed gates fail: median does not improve by 3%, and two measured pairs
are slower. Warmup is also slower and is retained, excluded from medians only
as predeclared. Reference times vary during the cohort; this does not establish
a universal regression either. No sample was selected away or retried.

Peak child RSS among measured trials is 405,744 KiB reference versus 401,380 KiB
scratch (4,364 KiB lower). This small memory observation does not replace the
predeclared speed gate. Filesystem output blocks are 3,381,408 in every run.
The earlier cost-only gain cannot be relabelled as a full-build improvement.

**Decision:** restore `tiler.py` byte-for-byte to `e651905`; do not ship the
scratch runtime, a flag or a fallback. Retain the 69 algorithm-independent
refit contracts, all evidence and experimental commits. The previously
qualified blocked distances and eight-column averages remain unchanged.
The post-restoration suite has 364 passing tests; scoped Ruff and diff checks
pass. No renderer, format, deployment or runtime behavior change is delivered.

## Provenance and reproduction

- Experimental runtime: `cfc28fbeff030e63ff238a23d80d4ffb1c47d677`.
- Measured clean experimental candidate with driver:
  `e9b554ef42931d990021e3569c966ede4d6cfc03`.
- Reference: `e651905f593cafe293d74d4d1ef8251e2cedd442`.
- Versioned [driver](gstile-refit-scratch-builds.mjs) and
  [results](gstile-refit-scratch-results.json): protocol, every trial, complete
  control inventory, report hashes and exact paired/median calculations.
- About 1.4 GiB retained at the evidence root: all bundles, raw reports,
  stdout/stderr, inventories, trials, protocol, independent binary checker and
  result, plus the reference worktree. No cleanup requested/performed.

Run from the **experimental candidate**, not the final restored delivery.
Create a fresh evidence directory with a detached checkout named `baseline`
at the reference commit; retain the recorded source path/hash, then run:

```sh
node docs/benchmarks/gstile-refit-scratch-builds.mjs /absolute/new-evidence-directory
```

Existing results are refused. The driver verifies pinned clean code/source,
each report and file, and source hash again after the cohort. Any changed
fixture, runtime or options needs a separate declared protocol. A future
sigmoid/allocation experiment must establish its own exactness and timings;
this result does not reject every possible refit optimization.
