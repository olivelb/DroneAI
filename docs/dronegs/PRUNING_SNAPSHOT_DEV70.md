# Dev.70 compact pruning snapshot

Date: 2026-08-27. Audit A4, third bounded phase after
[dev.69 Gaussian compaction](GPU_GAUSSIAN_COMPACTION_DEV69.md).
This changes transient refinement inputs, not the Gaussian ABI, PLY or checkpoint
wire format, training objective, production profile or browser renderer.

## Design and exactness contract

The CPU pruning code used only xyz, log-scale, scalar opacity and the finiteness
of the 15 opacity-SH coefficients from each 296-byte Gaussian. A GPU projection
now produces a 32-byte record: seven float32 values plus one uint32 finiteness
flag. Position, log-scale and opacity bits are copied without arithmetic.
The flag is the conjunction of 15 exponent-bit checks, rejecting every NaN and
infinity and accepting finite values, including subnormals and signed zero.
The check remains unconditional when opacity SH is disabled, matching dev.69.
Ignored color-SH/DC/quaternion fields do not acquire new rejection rules.

No exp, min/max, percentile, spatial threshold, score, Gumbel key, top-K, split
or decay equation moves to GPU or changes. The CPU visits identical input
values in identical order, including the maximum-opacity fallback when no
Gaussian survives. All five refinement-statistic arrays retain their old path.

The projection borrows existing `sh_rest_gradient` storage, with a 16 MiB
working-set cap. Prior Adam work and packing are ordered in the same default
stream. Each synchronous D2H copy finishes before the next chunk overwrites
scratch. There is no new device allocation, explicit device-wide fence or
CUDA event. Chunking introduces more implicit copy synchronization than the
old single Gaussian download; timings include that cost. Bounded backward
zeros the gradient buffer before reuse, while FastGS does not consume it.
At one Gaussian the existing 180-byte allocation already holds the 32-byte record.

NVIDIA's [CUDA 12.0 bit-reinterpretation contract](https://docs.nvidia.com/cuda/archive/12.0.1/cuda-math-api/group__CUDA__MATH__INTRINSIC__CAST.html)
and [D2H completion contract](https://docs.nvidia.com/cuda/archive/12.0.1/cuda-runtime-api/api-sync-behavior.html)
were checked. Integer exponent inspection avoids reliance on floating-point
fast-math classification. No external implementation was copied.

Snapshot logical payload falls from `N * 316` to `N * 52` bytes, including
statistics: **83.54% fewer bytes**. At 5M, that is 1,580,000,000 to 260,000,000
bytes. Hard-compaction upload remains `survivors * 4`, download zero, and split
upload `added * 8`. The snapshot phase now includes projection and chunked D2H,
not just copying. See [current telemetry semantics](REFINEMENT_TELEMETRY.md).

## Protocol, inputs and provenance

Candidate source, clean when configured:
`a7af065e0b1bda064e3e9d8ef70f5faa18d55392`.
Reference: retained dev.69 binary from
`5a3ff228515233120f1f1ec22442491ce6c21012`, whose native source matches
pre-change main `0b84a4c365039ba70c987cc2d53f765326c009c3`.
Follow-up documentation does not alter the qualified code.

Same frozen Saint-Etienne door checkpoint and benchmark as dev.69:

```text
/home/olivier/droneai-door-retraining-saint-etienne-1mm/checkpoints/porte-strict-saint-etienne-1mm/full/training.ckpt
```

Input: 5,000,000 Gaussians, reference-absolute/FastGS, SH3, opacity SH **on**,
seed 42, 30,000 completed steps; size 4,540,000,774 bytes. SHA-256:
`2f33e7572dc749fbc07cce3ade9390cd9f25729e73d7a4751aa250588c8a26c9`.
The explicit opacity mode preserves this historical checkpoint and does not
change the scalar-opacity Production V1 default.

This is a manual prune-only diagnostic on final state, not a scheduled training
iteration. Reset statistics yield zero growth candidates, 120,986 pruned
Gaussians and 4,879,014 survivors. Each repetition reloads the same input;
context setup, load/checksum and output saving are outside the timed interval.
A benchmark-only fence includes GPU completion. One excluded warmup per group
saves the entire resulting checkpoint for `cmp` and SHA-256 comparison.

Run order: reference A (five measured calls), candidate (five), reference B
(three), all after an excluded warmup. Groups are sequential, not randomized
paired runs. Frozen scalar-opacity synthetic states at 32,768 initial Gaussians
cover no growth, hard compaction plus split, and full-capacity recycling. Each
arm has an excluded warmup and five measured calls; input states are the same
retained dev.68 checkpoints, not freshly trained independently per arm.

## Results and full-state parity

| Group | Median fenced refinement (s) | Min–max (s) |
|---|---:|---:|
| dev.69 reference A | 1.243152 | 1.236515–1.249500 |
| dev.70 candidate | 0.461238 | 0.457023–0.466499 |
| dev.69 reference B, after candidate | 1.256774 | 1.245146–1.327345 |

Isolated refinement falls **62.90% (2.70x)** versus A, and 63.30% versus B.
The effect exceeds observed variation; it is incremental over dev.69, not an
end-to-end training or browser-rendering speedup.

| Phase median / payload | dev.69 A | dev.70 |
|---|---:|---:|
| Host preparation (s) | 0.580070 | 0.061565 |
| Snapshot projection/download (s) | 0.145468 | 0.027219 |
| CPU pruning (s) | 0.370562 | 0.273801 |
| CPU compaction (s) | 0.015606 | 0.015691 |
| Compaction upload (s) | 0.002158 | 0.001860 |
| CPU scoring (s) | 0.025954 | 0.025494 |
| Device submission (s, not GPU elapsed) | 0.006914 | 0.007118 |
| Other / host destruction (s) | 0.092825 | 0.012789 |
| Public call, before benchmark fence (s) | 1.243137 | 0.428296 |
| Snapshot download bytes | 1,580,000,000 | 260,000,000 |
| Compaction upload bytes | 19,516,056 | 19,516,056 |

The smaller snapshot reduces allocation/destruction work and requested download
volume. CPU pruning also speeds up with smaller input records; a separate cache
profile was not collected, so this is not a quantified cache-miss claim.
Phase medians need not sum to the median total. Crucially, dev.70's public call
returns while roughly 33 ms of device work remains in this fixture; **0.461 s,
not 0.428 s**, is used for the speed claim. The preceding longer host cleanup
had hidden most of that tail. No production fence is added to align timings.

Warm-call `cudaMemGetInfo` deltas are zero for both variants; the first call
uses the same extra 2 MiB of driver/runtime memory. Whole-benchmark peak RSS
is effectively unchanged: reference A 4,442,860 KiB, candidate 4,440,912 KiB,
reference B 4,442,388 KiB. Loading/saving checkpoints is included in this peak,
outside the timed refinement. No lower whole-process or full-training peak
memory claim is made; isolated refinement peak RSS was not instrumented.

All three real saved outputs are byte-identical to dev.69's retained output:
4,430,145,486 bytes, including parameters, moments, statistics and progress.
SHA-256: `743e10f97750b8bc8014a6541f65aeee9cc8dd2f60b1e79c4600aac466f987fe`.
Input checksum is unchanged after qualification.

| Synthetic path | Pruned / added | dev.69 median (ms) | dev.70 median (ms) | Full checkpoint |
|---|---:|---:|---:|---|
| No growth | 0 / 0 | 4.117 | 2.589 | Exact |
| Hard compaction + split | 2,048 / 18,432 | 5.928 | 4.357 | Exact |
| In-place recycling | 128 / 128 | 4.102 | 2.992 | Exact |

Unlike the preceding compaction-only lot, the snapshot change applies to all
three paths. Observed reductions are 37.11%, 26.49% and 27.06%, respectively,
in this small synthetic protocol, not universal performance guarantees.
Both arms also match the preceding lot's corresponding full-state hashes:

- No growth: `6a6f95332ed4f4c74c4632e3658b84df7e720d9e0dd268ac3a25b735bf50a9cf`.
- Compaction: `519d002403701bc33f1ff492bde693f7a9a70e03a7403d4b76029b66e2cf62bb`.
- Recycling: `0d222aa2ee260c02838e8665069e89204748121c6cf0d9ba6db1ee594055c08b`.

## Hardware and validation

BIGZEN: WSL Ubuntu 24.04, Ryzen 9 5950X, 94 GiB RAM, RTX 3090 24,576 MiB,
driver 591.74, about 675 MiB desktop GPU use before qualification. CUDA 12.0.140,
CUDA host GCC 12, C++ GCC 13.3, CMake 3.28.3, Ninja 1.11.1, Python 3.12.3,
Release/native sm_86. No concurrent training was started for the benchmark.

Local: RTX 4070 Laptop 8,188 MiB, driver 610.62, CUDA 12.9.86, GCC 13.3,
Release/native sm_89, shared with the desktop. Existing image
`dronegs-dev:pixel-weighted-6865308`, ID
`sha256:2c74d65b960f1c867b7ac3c019666d71379e2b7c7066c5736716a9396d966edf`.
Local timings are regression evidence only, not performance claims.

- New snapshot transfer contract fails on dev.69 and passes on dev.70.
- Five direct projection cases cover 524,810 records in total, plus zero count.
  The CPU oracle compares all snapshot bytes and every opacity-SH finiteness
  result, independently using `std::isfinite`. Every lane is exercised with
  signed zeros, subnormals, finite extrema, infinities, quiet/signaling NaNs
  and positive/negative payloads. Exceptional ignored fields must not affect it.
- Cases cover singletons, odd sizes, one-row/17-row chunks and the 16 MiB cap.
  Host guards, unused scratch suffix, immutable source bytes, null/count/size/
  alignment guards are checked. Existing 44 bitwise compaction cases remain.
- Eight native suites pass on both GPU/runtime pairs, including the 400-step
  trainer test and bounded/FastGS compaction/split/resume with opacity SH off/on.
- Compute Sanitizer memcheck reports zero errors on the exact Ada test binary.
- 22 targeted Python contract/identity tests and repository static checks pass.
- All 52 telemetry objects pass JSON Schema, phase accounting and exact
  snapshot/compaction/split byte formulas, including warmups.
- Portable CUDA library compilation passes sm_75/80/86/87/89/90/100/101/120.
  Only sm_86 and sm_89 have execution evidence; only sm_86 has real timings.

The first test build exposed a harness mismatch: the new test compiled the
private device header without the production relaxed-constexpr option required
by `std::array::operator[]`. Only that test source now uses the same
`--expt-relaxed-constexpr;--use_fast_math` options as the production kernel.
The corrected red run reaches and fails the expected old-payload assertion;
the green run passes it. No production compiler setting or acceptance threshold
was weakened. The initial build failure and expected regression failure logs
are retained.

## Reproduction and retained evidence

BIGZEN evidence:
`/home/olivier/droneai-qualifications/pruning-snapshot-20260827`.
Exact source checkout/build, nine resulting checkpoints, JSONL, resource logs,
input/output hashes and build/run script are retained there. Script also at
`/home/olivier/run-pruning-snapshot-20260827.sh`.
The dev.69 reference binary remains in `gaussian-compaction-20260827/build`;
synthetic inputs and extracted JPEG headers remain in `gpu-compaction-20260827`.

Local evidence:
`/home/olivier/droneai-qualifications/pruning-snapshot-20260827-exact`.
Source archive, native/portable builds, test/sanitizer/static logs, mirrored
BIGZEN logs, analysis and scripts are retained. Initial test workspaces remain
in `pruning-snapshot-20260827-red`, `-red-v2` and `-green`.

Source archive `source.tar` SHA-256:
`8af9061965ea032a5949f2d5a99c9b6d8299ce6cec4fd6d622ffd1f987c8e228`.

| Binary | SHA-256 |
|---|---|
| Reference checkpoint benchmark, 3090 | `217aec4dfe12bd6bbef3eb011e67bf96ff78006c202b680f5c27b9425c33bc49` |
| Candidate trainer, 3090 | `0234b2ac7321253ada5c222afd2323f66bda5ffc4a3ebdc10cf84b5d7e496b62` |
| Candidate checkpoint benchmark, 3090 | `254c19789fff99559e55bce3ec0d500aaf48f90f516c64a6281c28fc5ac60d0c` |
| Candidate trainer, 4070 | `155b6970168e1989b9da8ecf0a42b8facfea18baf45f224007d107babc4329b7` |
| Candidate checkpoint benchmark, 4070 | `8029ef2f09bbce84bc5ecbe27a796419d721ba0c25b645d5ded1eb6b779c6f6f` |

BIGZEN build uses the same locally extracted JPEG headers as the reference,
without installing system packages:

```bash
PRIOR=/home/olivier/droneai-qualifications/gpu-compaction-20260827
cmake -S SOURCE/app1-colmap/dronegs -B BUILD -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DDRONEGS_CUDA_ARCHITECTURES=native \
  -DCMAKE_CUDA_HOST_COMPILER=/usr/bin/g++-12 \
  -DCMAKE_CXX_FLAGS="-I$PRIOR/jpeg-dev/sysroot/usr/include/x86_64-linux-gnu" \
  -DCMAKE_CUDA_FLAGS="-I$PRIOR/jpeg-dev/sysroot/usr/include/x86_64-linux-gnu" \
  -DJPEG_INCLUDE_DIR="$PRIOR/jpeg-dev/sysroot/usr/include" \
  -DJPEG_LIBRARY=/usr/lib/x86_64-linux-gnu/libjpeg.so.8 \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DDRONEGS_BUILD_TESTS=ON -DDRONEGS_BUILD_BENCHMARKS=ON
cmake --build BUILD -j4
ctest --test-dir BUILD --output-on-failure
/usr/bin/time -v BUILD/dronegs_checkpoint_topology_benchmark \
  CHECKPOINT 1 0 0 5 NEW_OUTPUT_DIRECTORY
cmp BASELINE/after-refinement.ckpt CANDIDATE/after-refinement.ckpt
```

No artifacts were deleted. Original input, viewer bundles/server and production
images are unchanged. This qualifies code and native binaries, not a production
image deployment. Reverting the implementation restores dev.69 snapshots;
there is no persisted-state migration.

## Scope limits

No end-to-end training, browser-FPS, first-view, cross-GPU bitwise training or
new scientific quality promotion is claimed. A full-state match after one
real refinement plus synthetic growth/resume tests does not replace long,
multi-scene, multi-seed scientific A/B. CPU percentile/pruning and scoring/top-K
remain separate optimization candidates; no layout migration or approximate
selection is introduced in this lot.
