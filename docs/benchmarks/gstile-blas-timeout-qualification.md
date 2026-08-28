# GSTile CLI: shorter OpenBLAS idle wait

2026-08-28. Candidate under qualification; no full-build gain accepted yet.

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
No cleanup is authorized. Results will be appended after the complete cohort.
