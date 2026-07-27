# Phase 4 portable recent-NVIDIA CUDA baseline

Date: 2026-07-26

Version: `0.5.0-dev.30`

## Decision

Accept architecture-neutral CUDA code as the DroneGS baseline. Remove the
dev.28 Ada-only CUB Policy610 dispatch and 64-register ceiling. Retain dev.29's
generic tile-local shared-memory backward batching.

Local builds default to CMake's `native` detection. Distributable builds use
the explicit `portable` preset so the resulting executable does not depend on
which GPU was visible to the builder.

## Architecture contract

| Build mode | CMake input | Result |
|---|---|---|
| Local default | `DRONEGS_CUDA_ARCHITECTURES=native` | Detect every GPU visible at configure time |
| Portable | `DRONEGS_CUDA_ARCHITECTURES=portable` | Real cubins for `75`, `80`, `86`, `87`, `89`, `90`, `100`, `101`, and `120` |
| Explicit | CMake architecture list | Compile exactly the requested targets |

The portable list covers CUDA 12.8's recent Turing, Ampere, Ada, Hopper, and
Blackwell targets, including the datacenter and embedded variants relevant to
the toolkit. The NVIDIA driver selects the matching cubin at runtime. A future
architecture not supported by CUDA 12.8 requires a newer toolkit and rebuild.

## Retained generic implementation

- deterministic 64-bit `(positive depth bits, source index)` ordering;
- compact 48-byte projected render records;
- public CUB `DeviceRadixSort::SortPairs` policy dispatch;
- tile-local cooperative shared-memory batches for ordered-alpha backward;
- progressive SH, complete deterministic MRNF lifecycle, and LPIPS tooling.

No architecture-conditioned kernel, CUB policy, register cap, or compiler
optimization remains in DroneGS.

## Automated evidence

Validation host:

- NVIDIA GeForce RTX 4070 Laptop GPU;
- compute capability 8.9;
- driver 610.62;
- CUDA toolkit 12.8.1 / nvcc 12.8.93.

Default `native` configuration:

- CMake detected architecture 89;
- `cuobjdump --list-elf` reported only `dronegs.1.sm_89.cubin`;
- all six core, CPU rasterization, CUDA loss, CUDA rasterization, CUDA
  training, and LPIPS-tool suites passed;
- binary identified itself as
  `DroneGS 0.5.0-dev.30 portable-CUDA shared-backward MRNF prototype`.

Headless `portable` configuration:

- configured without a visible NVIDIA driver;
- compiled and device-linked successfully;
- `cuobjdump --list-elf` reported `sm_75`, `sm_80`, `sm_86`, `sm_87`,
  `sm_89`, `sm_90`, `sm_100`, `sm_101`, and `sm_120`;
- the final executable size was 11,648,984 bytes.

The final fat binary also completed a one-iteration Gajan GPU smoke on the
physical `sm_89` host. It loaded 25 images, trained 21 and held out four,
updated 9,324 Gaussians, evaluated all held-out views, wrote a completed
dev.30 manifest, and left the source dense COLMAP mount read-only.

The RTX 3090 target is the validated compiled `sm_86` image. Runtime execution
on a physical Ampere card remains a separate hardware validation gate.

## Performance and quality scope

This change deliberately trades dev.28's measured approximately 4% Ada
training advantage for one portable code path. No COLMAP stage, source photo,
large-scene output, or existing benchmark artifact was modified.

The mathematical renderer, ordering, gradient traversal, optimizer, topology,
and quality paths are unchanged. The six-suite CUDA validation therefore
establishes functional non-regression on Ada. Cross-architecture quality and
throughput must still be measured on physical Ampere, Hopper, and Blackwell
hardware; compiling their cubins on Ada is not a substitute for running them.
