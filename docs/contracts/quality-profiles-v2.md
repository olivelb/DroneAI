# Quality profiles v2

> Historical v2 contract. The resident-block HQ qualification candidate and
> its promotion gate are defined in
> [`quality-profiles-v3.md`](quality-profiles-v3.md).

DroneAI exposes three active end-to-end profiles through
`GET /mission/parameters`. `fast-v1` remains the reproducible preview envelope;
the new Normal and High Quality profiles calculate Gaussian capacity from the
scene footprint, requested GSD and detected device memory. The previous
`normal-v1` and `high-quality-v1` definitions remain accepted only so a stored
mission can be inspected or replayed without silently changing its recipe.

| Active profile | Purpose | Image width | SIFT features | Iterations | Gaussian policy | Downscale |
|---|---|---:|---:|---:|---|---:|
| `fast-v1` | Coverage and pipeline preview | 1,600 px | 2,048 | 7,500 | fixed 1.5 M | 8 |
| `normal-v2` | Routine production | 2,400 px | 4,096 | 15,000 | adaptive, 3 M floor, 8 M ceiling, 16 px target spacing | 4 |
| `high-quality-v2` | Qualified high-detail production | 4,096 px | 16,384 | 30,000 | adaptive, 5 M floor, 12 M ceiling, 8 px target spacing | 1 |

`normal-v2` is the API and Dashboard default. Fast is intentionally a preview;
its successful completion does not qualify visual sharpness or survey quality.

## Adaptive capacity contract

Before training, the worker:

1. removes the most extreme radial sparse-point outliers;
2. fits the scene plane with PCA and measures its robust convex-hull area;
3. estimates the requested output pixel count as `area_m2 / gsd_m²`;
4. derives the surface target as `output_pixels / spacing_px²`;
5. applies the profile floor, operator ceiling and a GPU-memory ceiling;
6. divides the global capacity across active spatial partitions.

The GPU ceiling reserves 4 GiB plus 15% of the physical VRAM and budgets 1,280
bytes per Gaussian capacity slot. That per-slot value covers the native
trainer's Gaussian, projection, gradient, optimizer and refinement arrays. The
reserve covers CUDA context, decoded images, pixel objectives and transient
raster buffers. The selected inputs and effective caps are persisted in the
Gaussian training stage provenance; an operator override remains visible.

The formula is a safety policy, not a quality claim. It deliberately does not
map one Metashape dense point to one Gaussian. Dense multi-view points and
optimized anisotropic Gaussians represent different primitives and have very
different training-memory costs.

## Achieved-density GSD gate

Normal and High Quality no longer publish a raster merely because the
requested capacity was plausible before training. After filtering, the worker
compares the retained Gaussian count with the robust scene area and the
profile's target pixel spacing:

`required = ceil(area_m2 / (requested_gsd_m * spacing_px)^2)`

If the retained count is below this requirement, rasterization fails closed.
The error reports the achieved mean spacing and the coarsest safe remedy:

`minimum_gsd_m = sqrt(area_m2 / retained_gaussians) / spacing_px`

The operator must either use at least that GSD or increase total scene
capacity through geographic resident-block partitioning. This global check is
a necessary lower bound; the future core/buffer partition gate will add local
density checks so a dense region cannot conceal a sparse one. `fast-v1` is
exempt because it is an explicitly non-qualifying preview with fixed capacity.

## Villesèque reference calculation and qualification

The current robust Villesèque reconstruction footprint measures 209,414 m².
At 0.015 m/px it represents approximately 930.7 million requested output
pixels. On a 24 GiB RTX 3090 the v2 calculation gives:

| Profile | Surface target | VRAM ceiling | Effective operator-bounded cap |
|---|---:|---:|---:|
| `normal-v2` | 3.7 M | 13.7 M | 3.7 M |
| `high-quality-v2` | 14.6 M | 13.7 M | 12.0 M |

The full-scene BIGZEN qualification reached the 12 M training cap, finished
with 10,282,571 Gaussians (10,272,114 after filtering), and used 18,290 MiB
peak VRAM. Its 30,000-iteration training took 14,401 seconds and produced a
32,283 x 33,543 raster at 0.015 m/px. Held-out PSNR was 24.3634 and SSIM was
0.6524. This qualifies the HQ capacity and isolated DroneGS execution envelope
on the RTX 3090; it does not qualify survey accuracy or eliminate visible
low-texture smoothing. See the
[15 mm Villesèque HQ v2 benchmark](../benchmarks/villeseque-p4-hq-v2-15mm-2026-08-11.md).

That historical 15 mm product predates the achieved-density gate. Its retained
10.27 M population is below the approximately 14.5 M global target implied by
209,414 m² and an 8 px spacing, so the same monolithic request would now be
rejected instead of being labelled HQ. The intended remedy is the planned
geographic multi-block HQ path, not removal of the gate.

OVHcloud remains unqualified until the project has a real GPU quota and the
exact GPU SKU passes the same run.

A focused native allocation probe subsequently passed 12.0 M, 13.7 M and
16.0 M capacities on BIGZEN. The documented result keeps the production
ceiling at 12 M because its reduced 256 px fixture does not include the full
4K aerial transient footprint: see the
[BIGZEN capacity probe](../benchmarks/bigzen-gaussian-capacity-probe-2026-08-10.md).

## SAM3 confidence policy

SAM3 now defaults to confidence 0.75 when the caller does not provide a value;
explicit thresholds remain supported. This reduces the unsafe 0.20/0.50
defaults observed during exploratory campaigns but does not prove precision.
A release benchmark must use labelled masks or at least labelled object
presence/boxes and report precision, recall and false positives at several
thresholds. Vegetation is outside the current SAM3 qualification scope until a
semantic-segmentation benchmark chooses and validates a suitable model.
