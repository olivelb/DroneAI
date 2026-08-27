# Dev.68 device-side optimizer-state compaction

Date: 2026-08-27. Audit A4, first bounded phase. Status: native implementation
qualified on Ampere/Ada with exact full-state parity; long-training speedup
and new production-science qualification are not claimed.

## Outcome and invariants

Hard compaction no longer downloads, allocates/repacks and reuploads Adam
moments. A GPU gather selects the same stable CPU survivor indices into scratch,
then a D2D copy commits each chunk. All scalar/color-SH/optional opacity-SH
moments and the two previously compacted refinement-statistic arrays retain
their exact values and order. CPU pruning, percentile gates, scoring, Gumbel
keys, top-K, Gaussian gathering/upload, split and decay equations are unchanged.
Existing in-place recycling and no-compaction paths are unchanged.

The scratch allocation already exists: `sh_rest_gradient`. Previous Adam use
has finished at the existing snapshot download; FastGS does not consume this
buffer, and bounded backward clears it before reuse. The helper borrows at most
16 MiB, or less when the existing allocation is smaller. There is no new device
allocation, capacity growth, host/device fence or production flag. Survivor
indices temporarily reuse `refinement_indices` before its next split upload.

Correctness of in-place chunking: strictly increasing survivor indices satisfy
`source[d] >= d`. After gathering a chunk, its destination interval ends before
all later destinations and therefore all later sources. Gather, D2D commit and
the next gather are queued in the same stream, so an earlier write cannot
destroy a later source. Unordered parallel in-place scatter would not satisfy
this guarantee. The helper validates counts, arithmetic bounds, scratch size
and alignment; its private caller supplies sorted/in-range survivor indices.

This relies on documented CUDA stream ordering, not host timing. NVIDIA's
[stream synchronization contract](https://docs.nvidia.com/cuda/cuda-runtime-api/stream-sync-behavior.html)
and [memory-copy synchronization contract](https://docs.nvidia.com/cuda/cuda-runtime-api/api-sync-behavior.html)
were checked; no third-party implementation was copied. Kernel/copy APIs may
still incur driver blocking, which is why telemetry remains host-wall time.

## Real checkpoint and protocol

The retained Saint-Etienne door checkpoint contains 5,000,000 Gaussians,
reference-absolute/FastGS SH3, **opacity SH enabled**, seed 42, 30,000 completed
steps. It predates the scalar-opacity production default; the benchmark passes
opacity mode 1 explicitly to preserve this original state, not to change V1.

Input on BIGZEN:

```text
/home/olivier/droneai-door-retraining-saint-etienne-1mm/checkpoints/porte-strict-saint-etienne-1mm/full/training.ckpt
```

Input size: 4,540,000,774 bytes. SHA-256, verified again after qualification:
`2f33e7572dc749fbc07cce3ade9390cd9f25729e73d7a4751aa250588c8a26c9`.
Its manifest reports 145.011 s of refinement / 2,307.402 s of training in the
historical run. That motivates investigation; it is not the current A/B result.

The optional `dronegs_checkpoint_topology_benchmark` reads bounded V4/V5 header
metadata to construct a context, then delegates checksum, identity, payload
and runtime verification to the unchanged native loader. Each repetition
reloads the same original checkpoint. It times only refinement plus a
benchmark-only completion fence; context initialization, loading/checksum and
output checkpoint writing are outside the interval. The first call is an
excluded warmup whose complete output checkpoint is retained for bytewise A/B
comparison. The original input is never overwritten.

Important limit: this is a **prune-only diagnostic on the final checkpoint**,
not a normal scheduled training step. Its refinement statistics have been reset;
there are zero growth candidates. It prunes 120,986 Gaussians and retains
4,879,014. This qualifies a real large-state compaction, not a full trajectory.
The allocation uses a one-pixel placeholder because no raster pass is timed.

Reference source: `185e2a71da8534a6d0fd4cf6c7018c3aad12da18` (dev.67 plus
the benchmark only). Candidate: `fb9e8c6caa82f300e78e7c1c95aa1f1a74948df9`.
Both were clean when configured; follow-up documentation does not change code.

Host: BIGZEN WSL Ubuntu 24.04, Ryzen 9 5950X, 94 GiB RAM, RTX 3090 24 GiB,
driver 591.74. CUDA 12.0.140, CUDA host GCC 12, C++ GCC 13.3, CMake 3.28.3,
Ninja 1.11.1, Python 3.12.3, Release/native `sm_86`. Desktop GPU use was about
675 MiB outside benchmark contexts. No other training was running.

## Results

Run order: reference A (5 measured calls), candidate (5), reference B (3),
each preceded by one excluded warmup. These are sequential repeat groups,
not randomly interleaved independent training runs.

| Group | Median refinement (s) | Min–max (s) |
|---|---:|---:|
| Reference A | 7.620329 | 7.483233–7.752307 |
| Candidate | 2.281038 | 2.197263–2.405399 |
| Reference B, after candidate | 7.396489 | 7.351431–7.397767 |

The candidate reduces isolated refinement wall time by **70.07% (3.34x)**
against A, and 69.16% (3.24x) against B. The effect substantially exceeds
observed variation; do not apply this factor to whole training or the browser.

Reference A versus candidate phase medians and logical byte counters:

| Metric | Reference | Candidate |
|---|---:|---:|
| Host preparation (s) | 0.580095 | 0.587922 |
| Snapshot download (s) | 0.153668 | 0.157246 |
| CPU pruning (s) | 0.370596 | 0.377258 |
| CPU compaction (s) | 5.700461 | 0.899815 |
| Compaction download (s) | 0.291249 | 0 |
| Compaction upload (s) | 0.407224 | 0.137296 |
| Device submission (s, not kernel elapsed) | 0.000137 | 0.008078 |
| Snapshot download bytes | 1,580,000,000 | 1,580,000,000 |
| Compaction download bytes | 3,000,000,000 | 0 |
| Compaction upload bytes | 4,371,596,544 | 1,463,704,200 |

Compaction host/device payload falls 80.14%, by 5,907,892,344 bytes per call.
The real bottleneck was primarily CPU allocation/packing, unlike the apparent
transfer dominance of earlier small shared-GPU fixtures. The retained Gaussian
host gather now dominates compaction CPU time. Snapshot/pruning remain separate
future targets. Phase medians need not sum to the median total; pageable upload
can wait for earlier GPU compaction and is not a pure bandwidth measurement.

Warm-call `cudaMemGetInfo` differences are zero for both variants. Both first
calls consume the same additional 2 MiB of driver/runtime memory; do not mistake
that for an algorithm scratch allocation. Peak process RSS from `/usr/bin/time
-v` is 6,728,556 KiB for A versus 4,441,940 KiB for the candidate (-33.98%).
This includes loading and writing checkpoints, not just the timed function;
no full-training peak-VRAM claim is made.

## State parity and regression coverage

The saved real outputs (including all moments, parameters, statistics and
progress) are **byte-for-byte identical**, 4,430,145,486 bytes each. Reference
A, candidate and reference B match; shared SHA-256:
`743e10f97750b8bc8014a6541f65aeee9cc8dd2f60b1e79c4600aac466f987fe`.

Scalar-opacity synthetic checkpoints cover growth as well. Each variant has
one excluded warmup and three measured calls at 32,768 initial Gaussians:

| Path | Pruned / added | Reference median (ms) | Candidate median (ms) | Full checkpoint parity |
|---|---:|---:|---:|---|
| No growth | 0 / 0 | 4.235 | 4.185 | Exact |
| Hard compaction + split | 2,048 / 18,432 | 23.879 | 7.570 | Exact |
| In-place recycling | 128 / 128 | 4.316 | 3.913 | Exact |

The unchanged no-growth/recycling paths are controls, not claimed speedups.
Their saved checkpoint hashes, respectively:

- `6a6f95332ed4f4c74c4632e3658b84df7e720d9e0dd268ac3a25b735bf50a9cf`
- `519d002403701bc33f1ff492bde693f7a9a70e03a7403d4b76029b66e2cf62bb`
- `0d222aa2ee260c02838e8665069e89204748121c6cf0d9ba6db1ee594055c08b`

Additional gates:

- 8/8 native CTests on RTX 3090 / CUDA 12.0 and RTX 4070 Laptop / CUDA 12.9.86.
- 36 direct CPU-oracle gather cases: scalar components 1/3/4, float2 components
  15/45, empty/identity/sparse/shifted survivors, singleton and non-power-of-two
  lengths, overlapping source/destination ranges, untouched tails, signed zero,
  denormal/NaN bit patterns, scratch/count guards and the 16 MiB chunk cap.
- Full training suite includes bounded and FastGS checkpoint/resume with
  opacity SH off/on after compaction and split.
- Compute Sanitizer memcheck on the exact Ada training-test binary: zero errors.
- 22 targeted Python contract/identity tests and repository static checks pass.
- CUDA library compilation passes for `sm_75`, `80`, `86`, `87`, `89`, `90`,
  `100`, `101`, `120`. Only 86/89 have execution evidence, and only 86 has the
  real-checkpoint performance qualification in this lot.

## Reproduction and retained evidence

BIGZEN evidence root:
`/home/olivier/droneai-qualifications/gpu-compaction-20260827`.
It contains immutable source checkouts, both builds, JSONL, resource logs,
original-derived output checkpoints, input/output hashes and synthetic inputs.
Local Ada/static/portable evidence:
`/home/olivier/droneai-qualifications/gpu-compaction-20260827-exact`.
The earlier local smoke build is also retained as `gpu-compaction-20260827-local`.

Native/benchmark SHA-256:

| Binary | SHA-256 |
|---|---|
| Reference checkpoint benchmark, 3090 | `e9c43716249f3148b51782476ccde5a03e93f47411954b82628829a9d2c60678` |
| Candidate trainer, 3090 | `8951b1b89ba79aff032e1288b32db47801483e77a0d1d8bb2f730b67a0bfcf17` |
| Candidate checkpoint benchmark, 3090 | `d54854bb5abd470adc5b9cbd920b7974ee511ddd183aae7f103354656bab7f0b` |
| Candidate trainer, 4070 | `b9f0ba4598f6688edffb6b8fc089431180ea667398656e73cdc70dcb5ad90340` |
| Candidate checkpoint benchmark, 4070 | `f2ef6a135e3df6978ca08a286e16ba63e8d643c3f43b7a4ba3fb0879de4fb7de` |

Build each exact checkout with:

```bash
cmake -S SOURCE/app1-colmap/dronegs -B BUILD -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DDRONEGS_CUDA_ARCHITECTURES=native \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 \
  -DDRONEGS_BUILD_TESTS=ON -DDRONEGS_BUILD_BENCHMARKS=ON
cmake --build BUILD -j4
ctest --test-dir BUILD --output-on-failure
/usr/bin/time -v BUILD/dronegs_checkpoint_topology_benchmark \
  CHECKPOINT 1 0 0 5 NEW_OUTPUT_DIRECTORY
cmp BASELINE_OUTPUT/after-refinement.ckpt CANDIDATE_OUTPUT/after-refinement.ckpt
```

BIGZEN initially lacked JPEG headers. No sudo password was available, so the
295 kB `libjpeg-turbo8-dev=2.1.5-2ubuntu2` package was downloaded and extracted
under `jpeg-dev/sysroot`, without installing system packages. Both builds use
the existing `/usr/lib/x86_64-linux-gnu/libjpeg.so.8`, the extracted include
directory, and its `x86_64-linux-gnu` child in both CXX and CUDA flags. Failed
configure/build attempts and the corrected commands are retained; no test
threshold or compiler error gate was weakened. The local build/sanitizer use
the existing `dronegs-dev:pixel-weighted-6865308` CUDA 12.9.2 image.

The benchmark is not a production checkpoint-resume driver: do not use its
prune-only output as a promoted training result. No source checkpoint, viewer
bundle, server or visual baseline was changed. No benchmark artifact was deleted.

## Decision and next work

Promote this bounded implementation with unchanged scientific defaults and
wire contracts. Revert the compaction commit to restore host packing if needed;
checkpoints remain interoperable. GPU top-K, CPU/GPU Gaussian compaction,
snapshot reduction and hot/cold layout migration remain separate work. A new
long-training A/B is needed before advertising an end-to-end time reduction.
