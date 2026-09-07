# Testable checkpoint writer and optional streaming checksum

The V5 checkpoint serializer is extracted into the CPU-only `include/dronegs/checkpoint_writer.hpp`. `TrainingCheckpointSnapshot::write_to` delegates to it; snapshot capture, ownership, asynchronous scheduling and checkpoint loading are unchanged. This makes the real serializer and publication path directly testable without a GPU.

The optional streaming checksum is **disabled by default**. No production speedup, geometry improvement or visual-quality improvement is claimed.

## Build-only switches

- `DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM=0` (default): reread the temporary payload and compute FNV-1a64 as before.
- `DRONEGS_EXPERIMENTAL_STREAMING_CHECKPOINT_CHECKSUM=1`: update FNV-1a64 while writing each payload span. The accumulated byte count must match the temporary payload size. The eight-byte trailer is unchanged and excluded from its own checksum.
- `DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES=0` (default): no additional checkpoint event or phase instrumentation in the native writer bridge.
- `DRONEGS_PROFILE_CHECKPOINT_WRITE_PHASES=1`: emit a successful-write `checkpoint_write_phases` JSON event.

Both macros reject values other than0/1 at compile time. They are compiler definitions, not environment variables, command-line options, persisted settings or new CMake production defaults. Experimental builds must record their definitions and binary identity. The existing loader's checksum implementation remains unchanged.

## Compatibility and persistence scope

The order remains: serialize `.tmp`, flush/close, compute checksum, append/flush the trailer, fsync the temporary, rename with the existing `.previous` fallback, then sync the directory. Pair-to-planar conversion retains its4MiB buffer. No array, optimizer state, scalar width, format version or publication step is removed.

This extraction does not strengthen the existing durability guarantees. In particular, the stream-close error and directory-fsync result retain their original handling, and a failure of the fallback restoration itself is not covered by the injected tests. The tests are not a power-loss qualification. Errors that the existing path detects continue to fail rather than report success.

The frozen test reference contains the original writer from source SHA `95fed9a03d3893ade4a20a7aa605563ab52f5d63b972251656f737ad6af31164`, adapted only to host fixture names/types. Its checksum, synchronization and serialization bodies also match the pre-extraction repository implementation. Keep this reference independent of subsequent writer edits.

## CPU tests

The same CMake test subdirectory can be built standalone, without a CUDA toolkit, or as part of the normal DroneGS test build:

```sh
cmake -S app1-colmap/dronegs/tests/checkpoint_writer -B build/checkpoint-writer -DCMAKE_BUILD_TYPE=Release
cmake --build build/checkpoint-writer --parallel 2
ctest --test-dir build/checkpoint-writer --output-on-failure
```

In a normal DroneGS build, select these tests with `ctest --test-dir <build> -L checkpoint-writer --output-on-failure`. The persistence/fault fixtures are registered on Linux because they use `/dev/full`, POSIX behavior and `LD_PRELOAD`. The injector is a test-only shared library; it is never linked into DroneGS. When the test binary links a dynamic ASan runtime, the wrapper resolves that same runtime with `ldd` and preloads it before the injector; sanitizer checks remain enabled.

Both default-macro configurations0/1 run the same actual serializer in reference and streaming modes. Counts0,1,3,23301,23302 cover optionals, an embedded NUL string, nonzero moments, and SH component buffers immediately below/above4MiB. Every file is compared byte for byte against the frozen writer, checked with its FNV routine, and compared by full SHA256. Zero gaussians is a serializer edge case only; the existing checkpoint loader rejects that state.

The six CTests additionally cover replacement, corruption, truncation, truncated moment arrays, missing parent directories, failed writes, fsync failure, and failed publication with a previously published checkpoint. Each invocation creates a unique artifact directory under the test build tree and retains its logs/results and checkpoint files. Repeated test invocations therefore do not collide or delete earlier evidence. The two parity tests together retain about243MiB of checkpoint fixtures per full run; fault fixtures are small. No dataset or GPU is required.

## Qualification and measured limits

CPU parity/fault qualification passed locally and on the target Linux/WSL machine. A separate native qualification campaign exercised both checksum build modes and checkpoint restore/continuation on small GPU fixtures. Those checks establish compatibility for the exercised paths, not a performance benefit or new persistence guarantees.

A real, profiled CPU ABBA experiment reused one immutable5.6-million-Gaussian snapshot and wrote fresh5.085GB files to the same local J: mount. Completed calls were:

| Order | Checksum path | Wall time | Result |
|---|---|---:|---|
| A1 | reread | 270.747s | Whole-file SHA matches source |
| B1 | streaming | 244.215s | Whole-file SHA matches source |
| B2 | streaming | 244.481s | Whole-file SHA matches source |
| A2 | reread | censored at600s | Temporary retained, not published |

A1 spent29.360s in the reread/checksum phase; B spent about4.702s computing FNV inside serialization and avoided the full payload reread. The first pair's9.80% difference is descriptive only. The last A slowed substantially during payload writing and was stopped by the predeclared limit. Its cause was not isolated, no replacement run was selected, and the experiment does **not** supply a completed ABBA performance estimate. No unprofiled confirmation was run, so the production default remains unchanged.

Profiling adds two clock reads and an accumulation around each streaming hash update. `checksum_seconds` is nested inside `serialization_seconds` for streaming mode and must not be added twice. CPU and I/O counters around the helper include observation overhead. Passing a non-null `WriteTimings*` directly to the helper enables its measurements independently of the native bridge macro; use `nullptr` for an unprofiled helper call.

Local checkpoint timings do not establish end-to-end training acceleration or S3 upload/publication gains. This change does not modify cloud transport, compression, lossy representation or reconstruction geometry.
