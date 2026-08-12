# Quality profiles v3

DroneAI retains the three publicly selectable v2 envelopes while it qualifies
the geographic resident-block HQ implementation. `fast-v1`, `normal-v2` and
`high-quality-v2` therefore remain the profiles returned by
`GET /mission/parameters`; stored v1/v2 missions keep their exact recipes.

The reproducible `high-quality-v3` candidate is available to the local BIGZEN
runner and internal qualification tooling, but is deliberately not advertised
by the API or Dashboard before its representative-block seam gate passes.

| Candidate | Native image width | SIFT features | Iterations | Resident policy | Density target |
|---|---:|---:|---:|---|---:|
| `high-quality-v3` | 4,096 px | 16,384 | 30,000 | adaptive, 5 M floor, 12 M hard cap per buffer | 3.6 output pixels per unique core Gaussian |

## Resident sizing and execution

The worker estimates the robust surveyed area and calculates a merged-scene
density target rather than limiting the whole terrain to one GPU model:

`scene_target = ceil(area_m2 / (requested_gsd_m * spacing_px)^2)`

It then accounts conservatively for core/buffer overlap and chooses a compact
projected-ground grid whose resident cells stay at or below the operator and
VRAM caps. At approximately 209,400 m², 2 cm GSD, 3.6 px spacing and 20%
overlap, this resolves to about 40.4 M unique scene Gaussians, at least seven
resident cells, and no more than 12 M Gaussians loaded for one buffer.

Each cell is trained from calibrated footprint-visible native image crops.
Training, filtering and rasterization persist and reload one buffer model at a
time across Stage Jobs. The rasterizer snaps adjacent geographic cores to one
global pixel grid and writes each pixel from exactly one core while retaining
the surrounding buffer splats for boundary support. Artifacts record resident
and unique-core populations, bounds, transforms and per-cell model paths; a
global GPU merge is intentionally forbidden.

## Promotion gate

The candidate becomes publicly selectable only after one representative native
block records all of the following:

- peak VRAM within the 12 M resident envelope;
- achieved global and per-core density compatible with the requested GSD;
- no missing or duplicate pixel rows/columns at core boundaries;
- bounded seam colour and height discontinuities;
- deterministic artifact replay through training, filtering and raster Stage
  Jobs;
- acceptable wall time and existing PSNR/SSIM canary results.

Only after this short gate is it useful to run a complete BIGZEN E2E. The
historical v2 formula, runs and SAM3 policy remain documented in
[`quality-profiles-v2.md`](quality-profiles-v2.md).
