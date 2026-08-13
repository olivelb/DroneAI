# Silo projected-initialization Fast qualification — 2026-08-13

## Scope and verdict

This controlled BIGZEN experiment isolates the screen-space initialization of
DroneGS on a coherent Silo cell. Camp India was rejected as a benchmark input:
its reconstruction comes from COLMAP 3.5, has no RTK reference, and its legacy
point/image identifiers do not satisfy the current track-authoritative export
contract.

The experiment confirms that a strict 2 px initialization ceiling removes too
much coverage. An 8 px ceiling is the conservative projected policy: it leaves
the median sparse seed unchanged and clips only the excessive projected tail.
The final candidate additionally reaches the requested 1.5 M population by
iteration 3,600 and stops topology changes and position noise at that same
run-scaled boundary. It completes successfully and slightly exceeds the local
baseline SSIM, although the local baseline retains a 0.29 dB PSNR advantage.

This is a representative Fast training-cell qualification, not a full aerial
mission, seam, ortho/DEM, Normal 8 GiB, HQ, or facade release gate.

## Reproducible input

| Item | Value |
| --- | --- |
| Host | BIGZEN Ubuntu WSL2 |
| GPU | NVIDIA RTX 3090, 24 GiB |
| Source reconstruction | Silo2, track-authoritative export |
| Images | 96 central images copied to ext4 |
| Native image regions | 10% border removed, crop metadata retained |
| Sparse seed | 22,247 points, minimum track length 3 |
| Sparse observations | 129,447 retained; 68,109 outside crops rejected |
| Training views | 318 train, 46 held out |
| Iterations | 7,500 |
| Capacity | 1.5 M |
| Tile mode | 4, adaptive native crops enabled |
| Optimizer/raster | `reference-absolute` / `fastgs` |
| Random seed | 42 |

Moving the selected photographs from the Windows-mounted SSD to WSL2 ext4 was
material: the controlled runs completed in approximately five minutes rather
than being dominated by cross-filesystem image access.

## Initialization isolation

All rows use the original 25% adaptive-growth ceiling and the legacy position
noise duration so only initialization policy and maximum scale growth differ.

| Policy | Final Gaussians | PSNR (dB) | SSIM | Pixel-weighted PSNR (dB) | Training (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| local KNN, growth 54.6x | 1,016,739 | 22.1213 | 0.801792 | 18.9248 | 281.93 |
| projected 2 px, growth 8x | 1,168,656 | 19.5984 | 0.749962 | 14.1127 | 321.01 |
| projected 2 px, growth 54.6x | 1,168,374 | 20.7847 | 0.788690 | 17.7421 | 320.14 |
| projected 4 px, growth 54.6x | 1,139,924 | 21.5402 | 0.798926 | 18.2337 | 313.57 |
| projected 8 px, growth 54.6x | 1,088,097 | 21.5916 | 0.798987 | 18.4560 | 300.41 |

The raw projected sparse-scale distribution had a 3.20 px median, 9.15 px
p95, and a 90.62 px maximum. Two pixels therefore clamps most seeds; eight
pixels bounds the pathological tail while preserving ordinary sparse support.
The projected 4 px variant improved 21 of 46 held-out views relative to local
KNN, including the weakest local view by 4.84 dB, but incurred several large
losses on otherwise supported views. This distribution explains why aggregate
quality alone cannot justify a stricter default.

## Run-scaled schedule

For any operator-selected iteration count, the last topology-growth boundary
is the 200-step boundary strictly inside the first half of the budget. The
standard values are:

| Mode | Iterations | Topology/noise through | Fixed-topology convergence |
| --- | ---: | ---: | ---: |
| Fast | 7,500 | 3,600 | 3,900 |
| Normal | 15,000 | 7,400 | 7,600 |
| HQ | 30,000 | 14,800 | 15,200 |

The same formula applies to manual durations. Capacity-targeted growth is an
explicit operator control and is automatically active for resident
area/GSD-planned blocks. Its bounded split fraction is now 7–50%, sufficient
for a sparse Fast seed to reach 1.5 M without exceeding the hard cap.

## Final candidate

The final `fast-v2-candidate` run uses projected KNN at 8 px, 54.6x maximum
scale growth, capacity-targeted splitting and run-scaled position noise.

| Metric | Result |
| --- | ---: |
| Population at iteration 3,600 | 1,500,000 |
| Final population | 1,500,000 |
| Final PSNR | 21.8300 dB |
| Final SSIM | 0.802398 |
| Pixel-weighted PSNR | 18.5548 dB |
| Final loss | 0.132757 |
| Native training time | 411.60 s |
| Wall time | 424.30 s |
| Peak observed VRAM | approximately 3 GiB |

Relative to projected 8 px under the former schedule, exact capacity and the
shorter noise window improve PSNR by 0.24 dB and SSIM by 0.0034. Relative to
local KNN, SSIM improves by 0.0006 while PSNR and pixel-weighted PSNR remain
lower by 0.29 and 0.37 dB. The visual comparison confirms that ordinary
facade/roof views remain comparable while weak sky-dominated views are still
limited by missing geometric support. This Fast result therefore validates the
schedule and safety ceiling, not full photorealistic quality.

## Validation and remaining gates

- 8/8 native CUDA/CPU CTest suites pass on the RTX 3090 build, including
  rasterization, CUDA training, crop/tile training, SH parity and LPIPS tool
  tests;
- 31 focused orchestration/profile tests pass; 19 CuPy-only local tests are
  explicitly skipped because CuPy is absent from the development venv;
- 32 Dashboard unit tests, TypeScript type checking and targeted ESLint pass;
- the Fast preset uses 7,500 iterations and manual iteration selection has a
  500-step increment;
- manual initialization policy, projected sigma, scale-growth factor and
  capacity-targeted growth apply to both map and facade mission parameters.

Normal 15,000 on the 8 GiB envelope, multi-block HQ 30,000, and a facade
representative run remain separate GPU qualification gates before candidate
profile promotion.
