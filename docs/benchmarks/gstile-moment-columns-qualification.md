# GSTile moment attributes in bounded column blocks

2026-08-28. Implementation/protocol; complete-build qualification pending.

## Change and preserved numerical contract

`_average_group_attributes` computes all floating-point attribute means in
blocks of eight columns instead of materializing N-by-C float64 values,
their weighted copy and G-by-C averages together. The stack is owned even for
float64 input, so multiplication by mass can reuse it. Keep the same field
order, float64 multiplication, `np.add.reduceat` along each original group,
division by group mass and cast to the output field. No field is omitted,
including values overwritten later by centers, scales, rotation or refit.

Accumulate the finite-average check across **every** block, then use it at
the original final validation boundary. No source or mass/index input is
modified. The helper returns its own structured output and validity flag;
its matrix temporaries do not remain alive during the subsequent refit.
Temporary width is bounded, but row count still scales with N/G: this is not
a constant-memory algorithm or a total RSS ceiling.

No covariance/eigensolver, group membership, ordering, opacity fit, source ID,
worker/default, candidate-distance block, bundle format, renderer, durability
or publication change. Both V3 and V4 use the averaging path. The complete
timing cohort below is V4 only; V3 compatibility is tested, not a timing claim.

## Exploration and tests before timing

Clean reference `1df7b1617650500e772943bac11050474bbc2f82`. On a separate
65,536-record fixture with 74 fields, compare the original averaging expression
to blocks 8/16/32. Two group counts, one warmup per variant/group followed by
four forward/reverse orders; all 40 trials retained, exact output bytes and
validity every time. Median seconds:

| Groups | Original | 8 columns | 16 columns | 32 columns |
| ---: | ---: | ---: | ---: | ---: |
| 32,768 | 0.121194 | 0.078714 | 0.099653 | 0.158854 |
| 4,097 | 0.059812 | 0.033104 | 0.045279 | 0.061628 |

Eight columns motivates the implementation, **not** a full-build gain claim
or universal optimum. Source fixture SHA-256:
`e987927938541084833f2e97cec1eaaff392ef3fb255f30081e13eb6c3189526`.
Probe script retained at
`/home/olivier/droneai-qualifications/gstile-moment-columns-probe-20260828.py`;
protocol, every trial and summary in the sibling directory without `.py`.

Two initial helper tests fail before implementation (missing helper; stopped
at two failures). After implementation, three full-proxy fixture assertions
failed on uninitialized padding in NumPy field-selection copies (52 passed,
stop at three); no numerical field differed. Tests now exercise both compact
and padded layouts: every named field is compared bit-for-bit, with complete
record bytes required for compact layouts. No numeric tolerance was added.

**127 new tests pass**: original averaging expression frozen as test-only
oracle; float32/64, 1/7/8/9/16/74/79 fields, strided/read-only inputs, very
different positive masses, signed zero, source IDs above 2^63, singleton and
large groups, nonfinite values in first/middle/last blocks and unchanged
failure messages. Full V3/V4 proxies and error bytes across opacity SH widths
0/3/8/15, degenerate/extreme cases, complete bundles across workers 1/2 and
individual/multi-tile aggregate layouts. Unchanged covariance/refit operations
are shared with the integration oracle; full builds compare separate commits.
The full affected tiler/probe suite has **295 passing tests**; scoped Ruff,
Markdown link and diff checks pass before timing.

## Predeclared complete-build cohort

Reference: retained detached `1df7b1617650500e772943bac11050474bbc2f82`.
Pin clean implementation and driver commits before running. Source is the
same synthetic 1,048,576-record SH3/directional fixture, 310,380,399 bytes:
`/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply`,
SHA-256 `c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Local i9-13900H, 20 logical CPUs, Ubuntu WSL2; Python 3.12.3, NumPy 2.4.6,
python-zstandard 0.25.0. Not BIGZEN or the real Saint-Etienne 50 M dataset.

Both arms: V4 adaptive, 65,536 leaves, 131,072-record chunks, 16,384 proxies,
2 MiB aggregate target, **one pack worker**. One warmup per arm then four
measured pairs AB/BA/AB/BA. Fresh child per build; wall time includes the whole
build and durable writes, excludes fixture generation and result hashing.
No concurrent local tests/profilers, no source changes during the cohort,
no cache flush, sample exclusion or selective retry. Retain all failures.

Fixed gate before measurement: all bundle files identical, clean pinned
reports and unchanged fixture; **at least 3% median time reduction and every
measured pair faster**. Median averages the two central observations. This is
a practical acceptance rule, not statistical significance. Never lower the
threshold after seeing data. If it fails, restore production and preserve the
negative result rather than silently shipping the experiment.

At this pilot size packs hold one tile each; multi-tile integration is covered
by unit tests, not a throughput claim here. No repeat Chrome/GPU gate if all
canonical bytes match: renderer unchanged, no expansion of prior visual PLY
qualification. Do not add this percentage to previous optimizations.

Evidence root, retained without cleanup:
`/home/olivier/droneai-qualifications/gstile-moment-columns-20260828`.
