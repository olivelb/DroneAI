# Phase 4 GAJAN bounded image-cache development result

Date: 2026-07-24
Status: experimental scaling sub-gate; not a LichtFeld parity result

## Change

`0.5.0-dev.2` replaces eager float32 storage of every decoded image with RGB8
payloads in a lazy LRU cache capped at 256 MiB. The same RGB8 payload is copied
to CUDA and normalized inside the loss kernels. Dataset metadata remains small
and unbounded image residency is removed.

The cache records requests, hits, misses, evictions, capacity, peak resident
bytes, and cumulative synchronous decode time. Asynchronous prefetch is not part
of this increment.

## Workload

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| CUDA image | NVIDIA CUDA 12.8.1, Ubuntu 24.04 |
| Dataset | GAJAN smoke, 25 JPEG images |
| Training resolution | 600 x 336 |
| Gaussians | 9,324 fixed |
| Iterations / seed | 500 / 42 |
| Host cache capacity | 268,435,456 bytes |
| Repetitions | 3 |
| Development artifacts | `/home/olivier/droneAI-workspaces/dronegs-phase4-dev2-final-500{,-r,-r3}/` |

## Dev.1 versus dev.2

The dev.2 timing and loss values below are medians over the three final runs.
The dev.1 reference predates the repetition protocol and remains a single run,
so only the training-loop result is treated as a useful directional signal.

| Metric | dev.1 eager float32 | dev.2 lazy RGB8 median | Change |
|---|---:|---:|---:|
| Initial anchor L1 | 0.1274940 | 0.1274933 | equivalent |
| Final anchor L1 | 0.0983346 | 0.0983375 | equivalent |
| Decoded target peak | 60.48 MB estimated | 15.12 MB measured | -75.0% |
| Training loop | 1.1865 s | 1.0493 s | -11.6% |
| Wall time | 2.3434 s | 2.6490 s | inconclusive |

Every dev.2 run issued 502 image requests: 25 misses, 477 hits, and zero
evictions. All 25 downscaled GAJAN images fit in 15.12 MB. The dev.2 wall
times ranged from 2.3566 s to 3.0430 s because startup varied by more than the
training-loop delta; no wall-time improvement is claimed from this sample.

The performance numbers cover the same development rasterizer and are useful for
the storage change only. They are not comparable to LichtFeld's full MRNF
training because topology, compositing, loss, and optimized parameters differ.

## Cardinality gate

The native core test also streams two sequential passes over 2,048 synthetic
images through an eight-item cache. The test asserts that peak residency never
exceeds the byte capacity. This establishes a cardinality-independent memory
bound without allocating a large synthetic dataset.

## Decision

The large-scene memory sub-gate passes:

- decoded target storage is four times smaller;
- resident decoded payload is byte-bounded;
- oversize individual images fail explicitly;
- cache behavior and decode time are observable in the manifest;
- GAJAN convergence is unchanged;
- training-loop time improves directionally while end-to-end timing remains noisy.

Remaining work before a representative 1,000+ photograph gate:

1. asynchronous JPEG prefetch;
2. pinned double-buffered host-to-device staging;
3. a real large-flight dataset and repeated throughput/VRAM measurements;
4. ordered alpha compositing and held-out quality parity.

No `dronegs-v0.5.0` tag is created from this result.
