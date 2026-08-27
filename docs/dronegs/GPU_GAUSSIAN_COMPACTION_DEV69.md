# Dev.69 device-side Gaussian compaction

Date: 2026-08-27. Audit A4, second bounded phase after
[dev.68 optimizer-state compaction](GPU_COMPACTION_DEV68.md).

## Implementation and invariants

Hard compaction now gathers Gaussian records on device as well as optimizer
state. It reuses the existing typed, stable, chunked gather helper and the
existing disposable SH-gradient allocation, capped at 16 MiB. No additional
device buffer, stream, production fence, option or persisted format is added.
The five CPU scoring-statistic vectors are gathered in place in ascending
survivor order, then shrunk. The CPU Gaussian snapshot is not read after
pruning, so its second full-size host vector and H2D upload disappear.

The same invariant protects both gathers: strictly increasing survivor indices
satisfy `source[d] >= d`. A committed destination cannot overwrite any later
source. GPU gather and D2D commit use one stream, including across chunks;
CPU statistics are processed in destination order. The typed Gaussian copy
performs no mathematical transformation. Gaussian fields, survivor order,
Adam mapping, pruning/percentiles, scoring/Gumbel/top-K, split, decay and
V4/V5 checkpoint layout are unchanged. No-growth and in-place recycling paths
are unchanged. Bounded backward clears borrowed scratch before reuse; FastGS
does not consume it. At the smallest compactable population (two), the existing
360-byte SH-gradient allocation accommodates one 296-byte Gaussian record.

Per-call logical transfer formulas, with N inputs and S survivors:

- Snapshot D2H stays `N * 316` bytes (Gaussian plus five statistics).
- Hard-compaction D2H stays zero.
- Hard-compaction H2D falls from dev.68's `S * 300` to `S * 4` bytes.
- Parent/destination upload stays `added * 8` bytes.

Gaussian gather/D2D submission is now included in `device_submit_seconds`.
Removing Gaussian H2D can shift its implicit wait to later work. These fields
remain host API wall time, not independent GPU elapsed time. The benchmark's
completion fence, outside production, is included in all reported total times.
See [telemetry semantics](REFINEMENT_TELEMETRY.md).

## Frozen input and protocol

Candidate source, clean at configuration:
`5a3ff228515233120f1f1ec22442491ce6c21012`.
Reference: retained dev.68 binary from
`fb9e8c6caa82f300e78e7c1c95aa1f1a74948df9`, whose native source matches the
pre-change main at `ade0a6224191738e2186f6e4ce1e7d2ab044f841`.
Follow-up documentation does not change the qualified code.

The input is the same final Saint-Etienne door checkpoint as dev.68:

```text
/home/olivier/droneai-door-retraining-saint-etienne-1mm/checkpoints/porte-strict-saint-etienne-1mm/full/training.ckpt
```

5,000,000 Gaussians, reference-absolute/FastGS, SH3, opacity SH **on**, seed 42,
30,000 completed steps; 4,540,000,774 bytes. Input SHA-256:
`2f33e7572dc749fbc07cce3ade9390cd9f25729e73d7a4751aa250588c8a26c9`.
Opacity mode 1 preserves this historical state; it does not change today's
scalar-opacity Production V1 default.

This is a manually invoked prune-only diagnostic on final state, not a scheduled
training iteration. Statistics are reset, yielding zero growth candidates,
120,986 pruned Gaussians and 4,879,014 survivors. Each repetition reloads the
same original checkpoint. The benchmark excludes initialization, load/checksum
and checkpoint saving from the refinement interval. One excluded warmup per
group saves the entire resulting checkpoint for bytewise comparison.

Run order: dev.68 reference A (five measured calls), dev.69 (five), dev.68
reference B (three). These are sequential groups, not randomized paired runs.
Scalar-opacity synthetic checkpoints at 32,768 Gaussians separately exercise
no growth, hard compaction plus split, and full-capacity recycling; each arm
has one excluded warmup and five measured calls. The synthetic source states
are retained from dev.68, not independently retrained per arm.

## Results and full-state parity

| Group | Median refinement (s) | Min–max (s) |
|---|---:|---:|
| dev.68 reference A | 2.273990 | 2.211162–2.346828 |
| dev.69 candidate | 1.237630 | 1.228467–1.258732 |
| dev.68 reference B, after candidate | 2.230035 | 2.218144–2.422026 |

Isolated refinement time falls **45.57% (1.84x)** against reference A,
and 44.50% against B. The effect exceeds observed variation. This is the
incremental gain over dev.68, not over the original CPU moment implementation,
and not an end-to-end training or renderer speedup.

| Phase / payload | dev.68 A | dev.69 |
|---|---:|---:|
| Host preparation median (s) | 0.585319 | 0.579668 |
| Snapshot download median (s) | 0.150174 | 0.144252 |
| CPU pruning median (s) | 0.368373 | 0.368221 |
| CPU compaction median (s) | 0.931416 | 0.016094 |
| Compaction upload median (s) | 0.140172 | 0.001984 |
| CPU scoring median (s) | 0.017424 | 0.025561 |
| Device submission median (s, not GPU elapsed) | 0.006975 | 0.007074 |
| Snapshot download bytes | 1,580,000,000 | 1,580,000,000 |
| Compaction download bytes | 0 | 0 |
| Compaction upload bytes | 1,463,704,200 | 19,516,056 |

Hard-compaction upload falls 98.67%, saving 1,444,188,144 logical bytes per call.
Phase medians do not necessarily sum to the median total. CPU scoring is about
8 ms slower in this measurement, while CPU gathering/upload save about a second;
scoring math did not change. Do not infer individual kernel time from the table.

Warm-call `cudaMemGetInfo` deltas are zero for both variants. All first calls
consume the same additional 2 MiB of driver/runtime memory. Whole-process peak
RSS is effectively unchanged: A 4,442,420 KiB, candidate 4,442,904 KiB, B
4,442,384 KiB. This includes checkpoint loading/saving outside the timed region;
removing the host gather does **not** establish a lower process peak in this
protocol. No isolated refinement peak-RSS or full-training VRAM claim is made.

All three real output checkpoints are byte-identical to dev.68's retained
reference: 4,430,145,486 bytes, including parameters, moments, statistics and
progress. SHA-256:
`743e10f97750b8bc8014a6541f65aeee9cc8dd2f60b1e79c4600aac466f987fe`.
The original input checksum is unchanged after qualification.

| Synthetic path | Pruned / added | dev.68 median (ms) | dev.69 median (ms) | Full-state parity |
|---|---:|---:|---:|---|
| No growth | 0 / 0 | 3.926 | 4.139 | Exact |
| Hard compaction + split | 2,048 / 18,432 | 7.347 | 6.363 | Exact |
| In-place recycling | 128 / 128 | 4.386 | 4.416 | Exact |

The small synthetic compaction improvement (13.38%) is secondary to the real
large-state result. No-growth and recycling are unchanged-path controls, not
claimed speedups; their small variations should not be generalized.
Each pair also matches the preceding lot's corresponding saved checkpoint:

- No growth: `6a6f95332ed4f4c74c4632e3658b84df7e720d9e0dd268ac3a25b735bf50a9cf`.
- Compaction: `519d002403701bc33f1ff492bde693f7a9a70e03a7403d4b76029b66e2cf62bb`.
- Recycling: `0d222aa2ee260c02838e8665069e89204748121c6cf0d9ba6db1ee594055c08b`.

## Hardware and runtime

BIGZEN: WSL Ubuntu 24.04, Ryzen 9 5950X, 94 GiB RAM, RTX 3090 24,576 MiB,
driver 591.74, about 675 MiB desktop GPU use before qualification. CUDA 12.0.140,
CUDA host GCC 12, C++ GCC 13.3, CMake 3.28.3, Ninja 1.11.1, Python 3.12.3,
Release/native sm_86. No concurrent training was started for this test.

Local regression/sanitizer: RTX 4070 Laptop 8,188 MiB, driver 610.62, CUDA
12.9.86, GCC 13.3, Release/native sm_89. The desktop shares that GPU; no local
performance claim is made. Existing image `dronegs-dev:pixel-weighted-6865308`,
ID `sha256:2c74d65b960f1c867b7ac3c019666d71379e2b7c7066c5736716a9396d966edf`.

## Regression evidence

- The reduced-upload regression fails against the old implementation and
  passes with dev.69; the failure is retained, not reported as a qualification.
- 44 direct bitwise CPU-oracle gather cases: scalar fields 1/3/4, float2 fields
  15/45 and complete Gaussian records. Cases include empty/identity/sparse/
  shifted survivors, singleton/non-power-of-two populations, overlapping
  source/destination, untouched tails, signed zero, denormals, NaN payloads,
  bounds guards and buffers exceeding the 16 MiB cap.
- 8/8 native CTest suites pass on both GPU/runtime pairs. The training suite
  covers bounded and FastGS compaction/split/save/load/continued training with
  opacity SH both off and on, plus the 400-step trainer aggregate test.
- Compute Sanitizer memcheck reports zero errors on the exact Ada test binary.
- 22 targeted Python contract/identity tests pass; repository static checks pass.
- All 52 emitted telemetry objects pass JSON Schema, phase accounting and exact
  snapshot/compaction/split logical-byte formula checks (including warmups).
- The CUDA library compiles for sm_75/80/86/87/89/90/100/101/120. Only sm_86
  and sm_89 have execution evidence; only sm_86 has real-checkpoint timings.

The first static run reached `actionlint` but could not find it in the
non-login script's PATH. The unchanged gate passes when rerun through the
configured login shell, which exposes the existing `/home/olivier/.local/bin`
installation. Both logs are retained; no check was disabled or dependency changed.

## Reproduction and retained evidence

BIGZEN root:
`/home/olivier/droneai-qualifications/gaussian-compaction-20260827`.
It retains the exact source checkout, build, JSONL/resource logs, input/output
hashes and nine resulting checkpoints. Reference binary and synthetic input
checkpoints remain under the preceding `gpu-compaction-20260827` root.
Build/run script: `/home/olivier/bigzen-run.sh` (also retained in the evidence).

Local root:
`/home/olivier/droneai-qualifications/gaussian-compaction-20260827-exact`.
It retains source archive, exact native/portable builds, tests, sanitizer,
static checks, mirrored measurement logs and analysis. Initial red/green smoke
builds remain in `gaussian-compaction-20260827-red` and `-green`.
Source archive `source-5a3ff22.tar` SHA-256:
`5a03c19048b537c1c367ac0557479538683d1903a99f949594bbb45607d93ed2`.

| Binary | SHA-256 |
|---|---|
| Reference checkpoint benchmark, 3090 | `d54854bb5abd470adc5b9cbd920b7974ee511ddd183aae7f103354656bab7f0b` |
| Candidate trainer, 3090 | `0576321a68b836a3f37b06fa06d9b53ec3a09dd5dc98b49ed5686a55dbd6a452` |
| Candidate checkpoint benchmark, 3090 | `217aec4dfe12bd6bbef3eb011e67bf96ff78006c202b680f5c27b9425c33bc49` |
| Candidate trainer, 4070 | `5b507ce7b211f7c2da31d8c5797114d2a6d54a52e6926f8f39302299887c4b67` |
| Candidate checkpoint benchmark, 4070 | `0e8b1d664b77cb4303e21af4cfe091f02074992b5ca6f3599703acea718ede0e` |

Build uses the dev.68 local JPEG-header extraction, without system installation:

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

No benchmark artifacts were deleted. Source checkpoint, viewer bundle, server
and visual reference are unchanged. The code and qualified native binaries are
not a deployment of a new production image. Reverting the implementation commit
restores dev.68 host gathering with no checkpoint migration.

## Remaining limits and next step

No full-training, browser-FPS, first-view, cross-GPU bitwise training or new
scientific quality promotion is claimed. Real checkpoint parity covers one
refinement from final state; synthetic growth/resume checks complement it but
do not replace multi-scene, multi-seed long-training A/B qualification.
The next bounded target is host snapshot preparation and CPU/GPU snapshot
volume, while preserving CPU percentile/pruning decisions exactly. Hot/cold
layout migration and GPU top-K remain independent, unqualified changes.
