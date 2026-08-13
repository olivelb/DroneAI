# Quality profiles v3

DroneAI exposes `fast-v1`, the resident `normal-v3` and `high-quality-v2` from
`GET /mission/parameters`. Stored v1/v2 missions keep their exact recipes;
`normal-v2` remains available for replay but is no longer offered for new
missions.

`normal-v3` passed its real multi-block BIGZEN gate on 13 August 2026 and is
the default profile. `high-quality-v3` remains an internal candidate until its
full target-GPU gates pass.

Qualification deployments may set
`DRONEAI_QUALITY_PROFILE_CANDIDATES_ENABLED=true` to add `high-quality-v3` to
the API catalog and therefore to the Dashboard profile selector. The flag is
strictly boolean and defaults to `false`; it does not change stored profiles,
worker recipes or the public default. Direct replay remains able to resolve a
stored candidate ID even while the catalog hides it. The flag must be removed
after promotion rather than becoming a permanent production dependency.
With Helm, the equivalent temporary override is
`--set dashboardApi.qualityProfileCandidatesEnabled=true`.

| Profile | Native image width | SIFT features | Iterations | Resident policy | Density target |
|---|---:|---:|---:|---|---:|
| `normal-v3` (qualified) | 2,400 px | 4,096 | 15,000 | adaptive, 3 M floor, 8 M operator cap; VRAM-bounded per buffer | 8 output pixels per unique core Gaussian |
| `high-quality-v3` | 4,096 px | 16,384 | 30,000 | adaptive, 5 M floor, 12 M hard cap per buffer | 3.6 output pixels per unique core Gaussian |

The real Normal qualification covered 88,239.71 m² at 2 cm/pixel. The 8 GiB
planner envelope resolved it to four buffers of at most 1.8 M Gaussians and
3,501,321 retained unique-core Gaussians. Peak sampled VRAM was 3,859 MiB;
all held-out, retention, density and coverage gates passed. See
[`aerial-gcp-normal-v3-resident-8g-2026-08-13.md`](../benchmarks/aerial-gcp-normal-v3-resident-8g-2026-08-13.md).

For qualification on a larger GPU, operators can set
`DRONEAI_GAUSSIAN_VRAM_BUDGET_GIB=8`. The planner clamps the detected free and
total memory to that lower envelope, never raises the hardware limit, and
records the effective byte values in the capacity artifact. The override
changes partition sizing only; it does not fake or cap the native CUDA
allocator, so promotion still requires sampled peak VRAM at or below 8 GiB.

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
overlap, this resolves to about 40.4 M unique retained Gaussians, about 41.3 M
Gaussians before filtering, at least seven resident cells, and no more than
12 M Gaussians loaded for one buffer.

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

## Promotion gate

Each candidate becomes publicly selectable only after its target GPU records
all of the following:

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

The 12 August HQ run qualifies one resident block only. A multi-block HQ run
and a resident facade run remain required. The historical v2 formula, runs and
SAM3 policy remain documented in
[`quality-profiles-v2.md`](quality-profiles-v2.md).
