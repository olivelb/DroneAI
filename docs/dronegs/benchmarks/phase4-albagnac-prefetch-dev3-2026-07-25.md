# Phase 4 Albagnac asynchronous JPEG prefetch result

Date: 2026-07-25
Status: large-scene throughput sub-gate passed; not a LichtFeld parity result

## Change

`0.5.0-dev.3` precomputes the deterministic training-camera schedule and
decodes image N+1 while CUDA processes image N. A single persistent worker owns
one bounded in-flight slot. Only the main thread inserts decoded images into the
existing 256 MiB LRU, so cache references used for host-to-device copies are not
mutated concurrently.

The manifest now distinguishes:

- `image_decode_seconds`: cumulative JPEG decoder service time;
- `image_wait_seconds`: foreground time actually blocked on image availability;
- prefetches started, consumed, and already ready at demand time.

`data_loading_seconds` remains foreground loading time and now includes image
wait rather than overlapping decoder service. Timings can overlap and therefore
are not expected to sum to wall time.

## Workload

| Item | Value |
|---|---|
| GPU | NVIDIA GeForce RTX 4070 Laptop GPU |
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Registered / training images | 1,376 / 1,376 |
| Training resolution | 800 x 580 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Host LRU capacity | 268,435,456 bytes |
| Peak resident decoded bytes | 267,264,000 |
| dev.2 control | one same-session warm run |
| dev.3 result | median of three final persistent-worker runs |

The control output is
`/home/olivier/droneAI-workspaces/albagnac-dronegs-dev2-warm-control-500/`.
The final dev.3 outputs are
`/home/olivier/droneAI-workspaces/albagnac-dronegs-dev3-worker-500-r{1,2,3}/`.

## Result

| Metric | dev.2 warm control | dev.3 median | Change |
|---|---:|---:|---:|
| Initial anchor L1 | 0.10941325 | 0.10941347 | equivalent |
| Final anchor L1 | 0.09290817 | 0.09290993 | equivalent |
| Foreground data loading | 28.652 s | 1.528 s | -94.7% |
| JPEG decoder service | not separated | 26.344 s | observable and overlapped |
| Foreground image wait | not separated | 0.954 s | 96.4% below decoder service |
| Training compute/accounting | 40.907 s | 57.167 s | concurrent-work semantics |
| End-to-end wall | 70.908 s | 59.629 s | -15.9% |
| End-to-end iterations/s | 7.05 | 8.39 | +18.9% |

All three final runs started and consumed 499 prefetches. The median run found
439 already ready at demand time (88.0%). Cache behavior stayed at one hit, 501
misses, 309 evictions, and 267,264,000 peak resident bytes. One additional
decoded image can exist in the explicitly bounded in-flight slot.

The original dev.2 Albagnac run took 247.36 seconds because its 181.5-second
startup included a cold CUDA/JIT effect. It is retained as a cold-start record
but is not used for the throughput percentage above. The same-session warm
control avoids claiming that unrelated warm-up as a prefetch gain.

## Correctness and artifacts

- Native core, CUDA gradient, and GPU convergence suites pass.
- GAJAN-25 at 500 iterations retained final anchor L1 `0.09833682`.
- Albagnac final anchor loss varies by less than 0.000004 across final dev.3 runs.
- Final Gaussian count and PLY byte size remain 1,025,093 and 57,405,633.
- Representative dev.3 PLY SHA-256:
  `02b7aa0d3d8bafc76727804fb389bb2ef6bf99fe07d77dba3ab7baa6294029c2`.

The PLY byte hash is not expected to match dev.2 because atomic floating-point
accumulation is not bitwise deterministic, but the measured anchor loss is
equivalent.

## Decision

The asynchronous JPEG prefetch sub-gate passes:

- foreground decode stalls are almost entirely hidden on the 1,000+ image scene;
- end-to-end warm throughput improves materially;
- resident LRU memory remains byte-bounded;
- the in-flight allocation is explicitly bounded to one image;
- camera order, convergence, and output cardinality remain unchanged.

No `dronegs-v0.5.0` tag is created. The next performance increment is pinned
double-buffered host memory with asynchronous CUDA copies. The Phase 4 exit gate
still requires ordered alpha compositing, full parameter gradients, DSSIM,
progressive SH, held-out metrics, and LichtFeld quality parity.
