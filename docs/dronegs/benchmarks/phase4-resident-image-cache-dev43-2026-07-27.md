# Dev.43 bounded scene-resident image cache

Date: 2026-07-27
Status: accepted for the next convergence ablation; full 15k rerun deferred

## Change

The fixed 256 MiB decoded-image LRU is replaced with a scene-sized RGB8
budget bounded to the inclusive range 256 MiB–2 GiB. For Albagnac at the
strict 800x580 training size, the declared capacity is 1,915,392,000 bytes.
The deterministic schedule, JPEG output, prefetch ordering, CUDA math, and
dataset files are unchanged.

## Strict same-session Albagnac pilot

Both runs used seed 42, 1,000 iterations, the modulo-8 held-out split,
progressive SH, the 1.5 million cap, the LichtFeld-absolute optimizer,
LichtFeld pruning bounds, and the dev.42 structural FastGS rasterizer.

| Metric | dev.42 control | dev.43 cache | Change |
|---|---:|---:|---:|
| Wall | 82.758 s | 79.787 s | -3.6% |
| Training compute | 18.134 s | 20.945 s | thermal/run variance |
| Foreground image wait | 56.484 s | 50.875 s | -9.9% |
| Decoder service | 72.935 s | 64.804 s | -11.1% |
| Cache misses | 1,346 | 1,172 | -12.9% |
| Cache evictions | 1,154 | 0 | -100% |
| Peak decoded RGB8 RAM | 267,264,000 B | 1,631,424,000 B | bounded |
| Held-out PSNR | 19.045893 dB | 19.036949 dB | -0.008944 dB |
| Held-out SSIM | 0.495167 | 0.495167 | +0.000001 |

An earlier dev.43 pilot measured 80.294 s wall versus the original dev.42
record of 89.859 s (-10.6%). The same-session pair above is retained as the
conservative result.

At 1,000 steps, most training views are consumed only once, so this pilot
cannot expose the main 15k benefit. The completed dev.42 15k run recorded
15,129 misses and 14,937 evictions; a scene-resident cache can decode each
view once and reuse it over subsequent shuffled epochs.

## Decision

Keep the bounded cache:

- it eliminates LRU churn;
- it improves conservative short-run wall time;
- it does not alter image bytes or training math;
- it caps decoded-image RAM at 2 GiB, avoiding the unbounded host-memory
  behavior previously observed outside this implementation.

Do not spend another full 15k run on this change alone. Combine it with the
next independently tested convergence improvement, then rerun the strict
Albagnac benchmark if both pass their short gates.

Artifacts:

- dev.42 control:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev42-control-1000-cachebench/`
- dev.43:
  `/home/olivier/droneAI-workspaces/albagnac-dronegs-dev43-resident-cache-1000-r2/`
