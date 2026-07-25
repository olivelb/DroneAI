# Phase 4 Albagnac pinned target-staging experiment

Date: 2026-07-25
Status: rejected experiment; no implementation retained

## Question

Would two pinned RGB8 host buffers, two device target buffers, CUDA events, and
a non-blocking transfer stream materially improve the `0.5.0-dev.3` Albagnac
pipeline?

The prototype preserved the deterministic camera schedule and persistent JPEG
worker. It protected each slot with separate upload-complete and target-release
events, and measured CPU staging plus CUDA upload service.

## Workload

| Item | Value |
|---|---|
| Dataset | Albagnac Mavic 3E RTK Oblique8 |
| Images | 1,376 |
| Training resolution | 800 x 580 RGB8 |
| Fixed Gaussians | 1,025,093 |
| Iterations / seed | 500 / 42 |
| Uploads per run | 502 |
| Uploaded bytes per run | 698,784,000 |
| Repetitions | three per orchestration |

Prototype outputs are retained outside Git:

- early-consume orchestration:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev4-pinned-500-r{1,2,3}/`;
- late-consume orchestration:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev4-pinned-late-500-r{1,2,3}/`.

## Results

| Metric, median | dev.3 reference | Early consume | Late consume |
|---|---:|---:|---:|
| Final anchor L1 | 0.09290993 | 0.09291023 | 0.09291106 |
| Foreground image wait | 0.954 s | 1.233 s | 0.973 s |
| CPU pinned staging | not measured | 0.080 s | 0.081 s |
| CUDA upload service | not measured | 0.063 s | 0.066 s |
| End-to-end wall | 59.629 s | 61.821 s | 63.679 s |
| Wall change | reference | +3.7% | +6.8% |

The early-consume variant attempted to stage N+1 from inside render N. It
shortened the time available to the JPEG worker and increased foreground wait.
The late-consume variant restored the full decode window and queued the upload
after backward/Adam launch, but did not recover the dev.3 median.

The sequential run series also accumulated a thermal bias: wall time trended
upward and the laptop GPU was still at 77 degrees Celsius while nearly idle
afterward. That makes small timing differences unreliable. It does not change
the central result: measured target upload service was only about 0.13
milliseconds per image and 0.06 seconds over the complete 500-iteration run.
Even perfect overlap cannot yield a material end-to-end gain on this workload.

## Decision

Reject and remove the pinned double-buffer prototype:

- convergence was equivalent, but neither orchestration passed the performance gate;
- the theoretical saving is below ordinary run-to-run and thermal variance;
- two device targets, two pinned buffers, a stream, and six events add lifecycle
  complexity disproportionate to the measured cost;
- `0.5.0-dev.3` remains the current version and the working implementation.

Pinned staging can be reconsidered for substantially larger training targets,
batched multi-camera work, or a future renderer whose transfer profile is
measurably dominant. The next optimization should be selected from a GPU kernel
profile. Quality work remains higher priority: ordered alpha compositing,
anisotropic projection, full parameter gradients, DSSIM, and held-out parity.
