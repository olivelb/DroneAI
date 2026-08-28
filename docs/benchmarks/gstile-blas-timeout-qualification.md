# GSTile CLI: shorter OpenBLAS idle wait

2026-08-28. **Qualified for the standalone CLI: 15.40% less median build time,
all four measured pairs faster, all 630 files identical.**

## Scope and mechanism

The dedicated `tools/build_gstiles.py` process sets
`OPENBLAS_THREAD_TIMEOUT=16` only when the variable is absent, before importing
NumPy. It does **not** reduce BLAS thread counts or change arithmetic, proxy
membership, precision, packing, durability, publication or rendering.
An explicit value, including `0` or an empty string, is preserved. The final
CLI JSON reports the configured timeout; it is not part of the bundle.
The benchmark records inherited BLAS environment separately from that result.

Importing the CLI, calling its `main()` from another application, and using
the library or the long-lived stage worker do not install this default.
Those paths may have initialized NumPy already; do not mutate their shared
BLAS state. This qualification is **CLI-only**, not a deployed-worker speedup.
No dependency, global shell/service environment, threadpool setter or backend
switch is added. Non-OpenBLAS backends may ignore this variable; no gain claimed.

OpenBLAS's [runtime documentation](https://www.openmathlib.org/OpenBLAS/docs/runtime_variables/)
lists the startup variable. Its
[pthread server source](https://github.com/OpenMathLib/OpenBLAS/blob/v0.3.31/driver/others/blas_server.c)
clamps positive values to 4–30 and uses `1 << value` as the idle-wait budget.
**16 is a cycle-counter exponent, not milliseconds or a thread count**.
`0` leaves the compiled backend timeout unchanged and is the CLI opt-out.
The [OpenBLAS FAQ](https://www.openmathlib.org/OpenBLAS/docs/faq/)
discusses reducing idle contention. The actual compiled default is not
measured here; the experiment compares unset against 16, not an asserted 26.
[NumPy documents](https://numpy.org/doc/stable/reference/global_state.html)
that linear-algebra threading belongs to its BLAS backend.

## Exploration: thread count rejected as a transparent change

Clean reference `a0434f726f7651a616909c168a70c65d77092830`.
Local i9-13900H, 20 logical CPUs, Ubuntu WSL2, Python 3.12.3, NumPy 2.4.6.
Read-only OpenBLAS getters report **20 threads**, pthreads, Haswell kernel,
version `0.3.31.188.0`, `MAX_THREADS=64`. Library SHA-256:
`05c9f9eb89ee68a4b9d673184fa91c99587e736392c0c2d49180a8aa5303d080`.
All recorded thread controls were absent. No diagnostic package was installed.

Separate child processes tried default/1/2/4 threads, with one full build per
setting. These are exploratory observations, not qualified speed claims:

| Threads | Wall seconds | User CPU seconds | Numeric arrays different / 49 | Full bundle exact |
| ---: | ---: | ---: | ---: | --- |
| 20 (default) | 21.763 | 146.043 | 0 | reference |
| 1 | 18.318 | 16.045 | 10 | no |
| 2 | 23.974 | 21.824 | 7 | no |
| 4 | 20.748 | 35.998 | 5 | no |

Directional refit rounding and canonical bundle bytes change. This does not
prove a visible regression, but it prevents transparent delivery under exact
parity. No thread-count override is shipped; a future variant would need
its own quality qualification. No failed case is silently relabelled exact.

Follow-up: keep 20 threads and compare only idle timeout unset/16. All **49
arrays** and **63 bundle files** match exactly. One exploratory build changes
22.263 to 19.582 s; user CPU 147.159 to 17.117 s. This supports the idle
contention hypothesis but is not the confirmatory result below.
Arrays cover opacity widths 0/3/8/15, normal/saturated/signed-zero inputs,
129/8,193-record refits, multi-generation proxy record/error bytes, and a
65,536-record refit. Numeric arrays and all builds remain on disk.

Evidence: `/home/olivier/droneai-qualifications/gstile-blas-threads-probe-v2-20260828`
and `gstile-blas-timeout-probe-20260828` under the same parent. Scripts are
adjacent `.py` files. The initial thread probe failed before any timed build
because its standalone import path omitted the repository root. That script,
protocol and failure note remain in `gstile-blas-threads-probe-20260828`.

## Contracts and fixed confirmatory protocol

Eleven new contracts cover import/programmatic-help isolation, startup default,
preservation of six explicit timeout values and all thread-count controls,
plus exact complete V3/V4 bundles with workers 1/2 and multi-tile aggregation.
The first test harness incorrectly assumed `runpy.run_path()` added the script
directory to `sys.path`; seven import checks failed, while 368 tests passed.
The test harness now models script startup correctly; application behavior and
numeric tolerances were not relaxed. Original test source is retained.
The corrected suite has **375 passing affected tests**; scoped Ruff, Markdown
link and whitespace checks pass. No test or CI threshold was relaxed.

Pin clean baseline, runtime and driver commits before timing. Synthetic
1,048,576 SH3/directional records, **not real Saint-Etienne 50 M**, source
`/home/olivier/droneai-qualifications/gstile-parallel-builds-20260827/source.ply`,
310,380,399 bytes, SHA-256
`c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Same Python/NumPy/zstandard (0.25.0), machine and filesystem in both arms.
V4 adaptive, leaf 65,536, chunks 131,072, proxies 16,384, aggregate target
2 MiB, one pack worker. Two warmups then four measured pairs AB/BA/AB/BA,
fresh process/output each time. All inherited controls must be unset;
only the candidate CLI supplies timeout 16. No concurrent local tests or
profilers, edits during timing, cache flushing, exclusions or selective retry.
Retain all artifacts and failures.

Acceptance fixed before timing: **at least 3% median wall-time reduction and
every measured pair faster**, exact all-file inventory/binary parity, clean
pinned reports and unchanged source hash. Median averages the central two
values. This is a practical gate, not statistical significance or a guarantee
on other datasets/backends. A failed gate restores the runtime.

Whole-build wall time includes durable writes, not fixture generation/hashing.
Each aggregate here holds one tile; multi-tile correctness is a separate unit
contract. Byte-identical bundles with an unchanged renderer require no new
Chrome visual test and do not expand the previous PLY visual qualification.

Evidence root: `/home/olivier/droneai-qualifications/gstile-blas-timeout-20260828`.
No cleanup is authorized.

## Complete-build result and delivery

All ten builds completed without exclusions, retries or failed samples.
All 630 files (manifest, raw packs and Zstd sidecars) have the same SHA-256
inventory, independently confirmed by nine explicit-argv recursive binary
`diff -rq` comparisons against the first reference. Source unchanged, every
report clean and pinned, inherited controls unset throughout. Common bundle ID:
`sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`.

| Trial | Order | Reference seconds | Timeout 16 seconds | Time reduction |
| --- | --- | ---: | ---: | ---: |
| Warmup | AB | 21.163 | 18.558 | — |
| Pair 1 | AB | 20.864 | 18.301 | 12.28% |
| Pair 2 | BA | 22.209 | 18.077 | 18.61% |
| Pair 3 | AB | 21.713 | 18.328 | 15.59% |
| Pair 4 | BA | 21.456 | 18.219 | 15.09% |
| Measured median | — | **21.585** | **18.260** | **15.40%** |

Both predeclared gates pass. Median child user CPU is 147.801 versus 15.810 s,
about 89.3% lower; this is cumulative CPU, **not wall-time speedup or measured
energy consumption**. It supports idle contention as the cause, not a claim
that 89% of tiler arithmetic disappeared. Filesystem output blocks remain
3,381,408 per run. Measured peak child RSS is 401,424 versus 409,896 KiB:
8,472 KiB **higher** (2.11%). No memory reduction is claimed.

Keep the small CLI startup change and environment reporting. The library,
long-lived worker and renderer remain unchanged. Do not add prior gains or
generalize this synthetic V4 result to BIGZEN, real 50 M, V3 throughput,
other BLAS implementations or deployments that already set a short timeout.
For the backend's original idle policy, launch the CLI with
`OPENBLAS_THREAD_TIMEOUT=0`; existing explicit thread controls still take effect.
Do not set a process-wide policy inside an already-running worker.

## Provenance and reproduction

- Runtime: `6dc6a8718766b64c91bc006f861718bb9eb6b5f8`.
- Measured clean candidate/driver: `529a811c282f6240a177ee82d414e07de3e9d3d8`.
- Reference: `a0434f726f7651a616909c168a70c65d77092830`.
- Versioned [driver](gstile-blas-timeout-builds.mjs) and
  [results](gstile-blas-timeout-results.json) retain protocol, all trials,
  reference inventory, runtime metadata, report hashes and exact calculations.
- All raw reports, stdout/stderr, bundles, inventories, protocol, trials and
  the reference worktree remain at the evidence root. Both exploratory
  sweeps, numeric `.npy` arrays and initial harness failures remain separate.

From the measured candidate or a clean documentation-only successor, create
a **new** evidence directory and detached `baseline` checkout at the reference
commit, retain the pinned source and numeric probe evidence, then run:

```sh
node docs/benchmarks/gstile-blas-timeout-builds.mjs /absolute/new-evidence-directory
```

The driver refuses existing outputs, explicit inherited controls, changed
runtime/library/source, dirty reports or numerical differences. Any other
environment needs its own declared protocol; no silent substitution or rerun.

## Follow-up profile, outside the timing cohort

A separate clean `529a811` cProfile build uses the same fixture/options and
CLI timeout. Its complete bundle is binary-identical to the reference.
The instrumented total is 18.587 s and is **not another benchmark sample**.
Inclusive costs: moment matching 6.160 s, candidate edges 5.746 s, of which
blocked distances 2.242 s; averaging 2.191 s and refit 1.981 s are included
inside moment matching. Do not sum nested costs. File splitting is 1.395 s,
Q96 encoding 1.102 s, and fsync 0.468 s on this synthetic pilot.

This points back to V4 candidate/group processing before another compression
or fsync change. It does not authorize reduced durability or predict a future
gain. Profile, text summary, metadata and exact bundle are retained at
`/home/olivier/droneai-qualifications/gstile-blas-timeout-profile-20260828`;
the adjacent `.mjs` driver is also retained. No experiment output was deleted.
