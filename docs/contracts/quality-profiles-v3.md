# Quality profiles v3

DroneAI retains the three publicly selectable v2 envelopes while it qualifies
the planar resident-block implementations. `fast-v1`, `normal-v2` and
`high-quality-v2` therefore remain the profiles returned by
`GET /mission/parameters`; stored v1/v2 missions keep their exact recipes.

The reproducible `normal-v3` and `high-quality-v3` candidates are available to
the local BIGZEN runner and internal qualification tooling, but are deliberately
not advertised by the API or Dashboard before their target-GPU gates pass.

| Candidate | Native image width | SIFT features | Iterations | Resident policy | Density target |
|---|---:|---:|---:|---|---:|
| `normal-v3` | 2,400 px | 4,096 | 15,000 | adaptive, 3 M floor, 8 M operator cap; VRAM-bounded per buffer | 8 output pixels per unique core Gaussian |
| `high-quality-v3` | 4,096 px | 16,384 | 30,000 | adaptive, 5 M floor, 12 M hard cap per buffer | 3.6 output pixels per unique core Gaussian |

For a representative 209,400 m² scene at 2 cm/pixel, an 8 GiB device resolves
`normal-v3` to about 8.2 M retained Gaussians across eight resident buffers.
Each buffer targets about 2.1 M Gaussians below the conservative 2.3 M detected
VRAM ceiling. This is a planning contract, not yet a real-GPU qualification.

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
time across Stage Jobs. The rasterizer snaps adjacent geographic cores to one
global pixel grid and writes each pixel from exactly one core while retaining
the surrounding buffer splats for boundary support. Artifacts record resident
and unique-core populations, bounds, transforms and per-cell model paths; a
global GPU merge is intentionally forbidden. PLY reads and writes use bounded
250,000-row host staging; filtering queries camera distance in the same bounded
chunks. Raster culling geometry is transformed once per resident model and the
CuPy pool is reused between output tiles.

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
Those measurements are evidence-only until a multi-block run defines
defensible thresholds; they cannot silently pass or fail a production run.

The 12 August HQ run qualifies one resident block only. A multi-block HQ run,
the real 8 GiB Normal run and a resident facade run remain required. The
historical v2 formula, runs and SAM3 policy remain documented in
[`quality-profiles-v2.md`](quality-profiles-v2.md).
