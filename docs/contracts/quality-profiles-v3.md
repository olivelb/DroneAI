# Current production quality profiles

Production catalog after the 2026-08-28 cleanup: `fast-v2`, `normal-v3`
(the unchanged default), and `high-quality-v4`. The operator confirmed that
Fast v2 and HQ v4 are qualified and will be used in the next campaign.
This records that decision; the cleanup does not claim a new GPU qualification.

The API, Dashboard and local runners use the same registry in
`shared/quality_profiles.py`. Historical replay profiles and the candidate
catalog/environment/Helm gate have been removed. Unknown or retired IDs fail
validation rather than silently selecting another recipe. The current facade
identity remains `DRONEGS_FACADE_HD_V3`; its V1/V2 replay identities are removed.

| Profile | Native image width | SIFT features | Iterations | Gaussian capacity | Initialization |
|---|---:|---:|---:|---|---|
| `fast-v2` | 1,600 px | 2,048 | 7,500 | fixed 1.5 M, monolithic | projected KNN, 8 px sigma, targeted growth |
| `normal-v3` | 2,400 px | 4,096 | 15,000 | adaptive, 3 M floor, 8 M operator cap; VRAM-bounded per buffer | local KNN, 2 px sigma |
| `high-quality-v4` | 4,096 px | 16,384 | 30,000 | adaptive, 5 M floor, 6 M cap per buffer | projected KNN, 8 px sigma, targeted growth |

Normal targets 8 output pixels per unique core Gaussian; HQ targets 3.6.
Initialization overrides are rejected for new missions: choose a qualified
profile instead of silently changing its initialization policy. Other supported
expert overrides are recorded as before; immutable profile identity cannot be
overridden.

Capacity-targeted growth is enabled for Fast v2 and HQ v4. Growth, pruning and
position noise stop on the final 200-step boundary strictly inside the first
half of the selected budget. The second half is fixed-topology convergence.
The cleanup does not change those formulas or numerical implementations.

The prior Normal multi-block qualification remains dated evidence:
[`aerial-gcp-normal-v3-resident-8g-2026-08-13.md`](../benchmarks/aerial-gcp-normal-v3-resident-8g-2026-08-13.md).
For a controlled 8 GiB qualification on larger hardware, the existing
`DRONEAI_GAUSSIAN_VRAM_BUDGET_GIB=8` planner override only lowers the planning
envelope; it neither raises hardware limits nor caps the CUDA allocator.

## Resident sizing and execution

The worker estimates the robust surveyed area and calculates a merged-scene
density target rather than limiting the whole terrain to one GPU model:

`scene_target = ceil(area_m2 / (requested_gsd_m * spacing_px)^2)`

This is the required **retained** population. Resident HQ training targets a
pre-filter population derived from a 98% retention policy:

`training_target = ceil(scene_target / 0.98)`

Both targets are rounded to the capacity quantum and recorded in the capacity
plan. The final density gate remains strict against `scene_target`; the reserve
does not relax the requested GSD.

It then accounts conservatively for core/buffer overlap and chooses a compact
metric product-plane grid whose resident cells stay at or below the operator
and VRAM caps. At approximately 209,400 m², 2 cm GSD, 3.6 px spacing and 20%
overlap, this resolves to about 40.4 M unique retained Gaussians and about
41.3 M Gaussians before filtering. The planner increases the number of
resident cells as needed and never loads more than the selected profile cap
for one buffer. For `high-quality-v4`, that hard cap is 6 M Gaussians.

For maps, that plane is projected ground from the geographic Sim3. For facades,
it is the fitted horizontal/vertical wall frame converted to metres. Each cell
is trained from calibrated footprint-visible native image crops.
Training, filtering and rasterization persist and reload one buffer model at a
time across Stage Jobs. The rasterizer snaps every buffer to one global pixel
grid, gives each core unit weight and linearly fades through its margins.
Overlapping RGB and height samples are accumulated and normalized in bounded
row chunks. This `linear-core-buffer-v1` contract avoids hard seams without
spatial resampling. Artifacts record resident and unique-core populations,
bounds, transforms and per-cell model paths; a global GPU merge is
intentionally forbidden. PLY reads and writes use bounded 250,000-row host
staging; filtering queries camera distance in the same bounded chunks. Raster
culling geometry is transformed once per resident model and the CuPy pool is
reused between output tiles.

## Next campaign

The fresh campaign must record the following on the target GPU:

- peak VRAM within its detected resident envelope (8 GiB for Normal, RTX 3090
  for HQ);
- achieved global and per-core density compatible with the requested GSD;
- no missing or duplicate pixel rows/columns across several core boundaries;
- bounded seam colour and height discontinuities;
- deterministic artifact replay through training, filtering and raster Stage
Jobs;
- acceptable wall time and existing PSNR/SSIM canary results.

The raster Job writes `gaussian_seam_report.json` with per-core-boundary RGB
and height mean/p95 jumps plus a boundary-to-nearby-interior gradient ratio.
Those measurements remain evidence-only until several independent datasets
define defensible thresholds; they cannot silently pass or fail a production
run. On the Normal qualification, feathering reduced boundary-to-neighbouring
gradient ratios to 1.00–1.06 for RGB and 1.00–1.02 for height.

Dated reports may describe former recipes or incomplete campaigns. They are
not operational instructions and do not override the production catalog above.
The next campaign must use new runs and current artifacts, not historical replay.
