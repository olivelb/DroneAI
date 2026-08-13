# Silo Normal-v4 8 GiB qualification — 2026-08-14

## Scope

This BIGZEN experiment qualifies the projected, capacity-targeted Normal-v4
training recipe on the same coherent Silo cell used for the Fast-v2 isolation.
It tests the 15,000-iteration schedule, exact 3 M resident capacity,
checkpoint/resume determinism and the memory envelope. It is not a multi-block
ortho/DEM, seam or facade release gate.

Camp India is deliberately excluded: its reconstruction was produced by
COLMAP 3.5 without RTK and does not satisfy the current track-authoritative
benchmark contract.

## Input and recipe

| Item | Value |
| --- | --- |
| Host | BIGZEN Ubuntu WSL2 |
| GPU | NVIDIA RTX 3090, 24 GiB |
| Source | Silo2 track-authoritative central cell |
| Source images | 96, copied to WSL2 ext4 |
| Sparse seed | 22,247 points |
| Native training views | 318 train, 46 held out |
| Iterations | 15,000 |
| Resident capacity | 3,000,000 |
| Image envelope | factor 4, 2,400 px maximum, adaptive native crops |
| Initialization | projected KNN, 8 px maximum initial sigma |
| Topology/noise boundary | iteration 7,400 |
| Optimizer/raster | `reference-absolute` / `fastgs` |
| Random seed | 42 |

## Capacity-estimator defect and correction

The first full control run used the run-scaled schedule but estimated the last
split fraction from earlier pruning ratios. It finished at 2,980,181
Gaussians, 19,819 below the requested population (0.66%). Its quality remained
valid—21.9285 dB PSNR, 0.796194 SSIM and 19.0379 dB pixel-weighted PSNR—but a
hard operator capacity must not depend on a noisy final-window estimate.

The correction requests the bounded 50% candidate pool in the last topology
window and delegates the final count to the existing deterministic hard-cap
selection. The replay reached exactly 3,000,000 Gaussians at iteration 7,400,
saved a 2.724 GB checkpoint and paused successfully. Resuming the same recipe
restored iteration 7,400 and the exact population before continuing fixed-
topology convergence; the first resumed sample at iteration 7,500 still held
3,000,000 Gaussians.

## Final result

| Metric | Result |
| --- | ---: |
| Final population | 3,000,000 |
| Final PSNR | 21.8600 dB |
| Final SSIM | 0.793183 |
| Pixel-weighted PSNR | 18.5909 dB |
| Final loss | 0.165936 |
| Resumed training time | 1,262.59 s |
| Resumed wall time | 1,273.48 s |

The 2,980,181-Gaussian control remains slightly higher by 0.0684 dB PSNR,
0.00301 SSIM and 0.447 dB pixel-weighted PSNR. Exact capacity therefore
qualifies the resource contract and deterministic final-window behavior; it
does not independently establish a quality gain. The held-out comparison
still shows blur where crop support is weak, while well-supported silo and
ground structure reaches 24.94–27.31 dB and 0.84–0.91 SSIM.

The resumed manifest exposed one metadata-only defect: checkpoint V4 retained
the initial aggregate PSNR/SSIM but not their pixel-weighted variants. Final
quality was unaffected. DroneGS dev.49 moves new checkpoints to V5, persists
both weighted values and retains V4 read compatibility; its CUDA round-trip
test covers all four initial metrics.

## Resource observations

The growth half peaked at 5,292 MiB VRAM. The resumed fixed-topology half
peaked at approximately 5.3 GiB while keeping the RTX 3090 at 98–100% compute
utilization. This leaves enough margin for the intended 8 GiB Normal envelope;
the current bottleneck is arithmetic throughput rather than VRAM.

Each 3 M checkpoint takes approximately 14 seconds and occupies 2.724 GB.
Checkpoint cadence is therefore a measurable reliability/performance tradeoff,
but changing it is not part of this scientific comparison because it does not
affect model quality.

## Validation and remaining gates

- the exact-cap native build passes all 8 CUDA/CPU CTest suites;
- 110 focused orchestration, profile and Gaussian tests pass;
- all 20 facade profile/orthophoto tests pass, including Normal-v4 and
  High-Quality-v4 parameter preservation;
- all 34 Dashboard tests, TypeScript type checking and ESLint pass;
- candidate catalog exposure remains opt-in through
  `DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED=true`.

This representative-cell Normal gate is closed. Multi-block HQ 30,000 and a
representative facade run remain separate target-GPU gates.

After the metrics and visual evidence were copied out, the disposable run
outputs and V4-to-V5 smoke checkpoint were removed from BIGZEN. The 964 MiB
prepared Silo cell, dev.49 build and scripted quality gates were retained; the
run remains reproducible from the recipe above.
