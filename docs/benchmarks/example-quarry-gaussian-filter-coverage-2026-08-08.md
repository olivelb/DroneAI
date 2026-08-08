# Example Quarry Gaussian filter coverage — 2026-08-08

The complete execution environment, stage timings, published artifacts and
acceptance status are recorded in
[`example-quarry-e2e-2026-08-08.md`](example-quarry-e2e-2026-08-08.md).

## Scope

The full `example_quarry_2.0` mission was executed on an RTX 3090 from Git
revision `6f120f927dc70f1a2059bd5bc71336251efeca75`. COLMAP registered 344 of
347 images and DroneGS completed 15,000 iterations with 1,500,000 Gaussians.
The held-out canary passed (`PSNR=22.0615`, `SSIM=0.6095`), but the published
orthomosaic was visibly sparse and washed out.

## Root cause

The map-space Sim(3) scale is `88.2641` metres per COLMAP model unit. After
that scale was applied, the former absolute Gaussian scale cap of `1.0` metre
retained only 410,583 of 1,500,000 primitives (27.4%). The following opacity
filter removed only another 205 primitives, so opacity was not the cause.

Raw maximum-axis scale statistics after georeferencing:

| Percentile | Scale |
| ---: | ---: |
| 25% | 0.948 m |
| 50% | 1.570 m |
| 75% | 2.723 m |
| 90% | 4.743 m |
| 95% | 6.799 m |
| 99% | 13.398 m |

The same canary-approved checkpoint was rendered at 0.20 m/px with controlled
filter variants; no retraining occurred.

| Maximum scale | Gaussians after scale + opacity | Retention | Non-white preview coverage |
| ---: | ---: | ---: | ---: |
| 1 m | 410,378 | 27.4% | 24.6% |
| 3 m | 1,173,146 | 78.2% | 62.3% |
| 5 m | 1,358,194 | 90.5% | 66.7% |
| 10 m | 1,460,081 | 97.3% | 70.2% |
| disabled | 1,500,000 | 100% | 75.9% |

Five metres restored the quarry footprint while still removing the large,
blurred peripheral splats visible at 10 metres and with filtering disabled.

## Corrective contract

- Map products default `gs_filter_max_scale` to `5.0` projected metres.
- Facade products retain their separate `facade_filter_max_scale=1.0` local
  model-unit default.
- `gs_filter_min_retained_ratio=0.80` rejects destructive cleanup before a
  sparse product can be promoted. Operators can override the gate explicitly
  for an exceptional scene.
- The gate complements the DroneGS held-out canary: the canary measures
  view-space appearance before map-space filtering, while retention protects
  the later orthographic product.

The corrected 2 cm render is 64,112 by 50,994 pixels. Its uncompressed RGB
payload exceeds the classic TIFF 4 GiB limit, so both RGB and height products
now use GDAL's `BIGTIFF=IF_SAFER` creation policy. Small outputs remain classic
TIFFs; GDAL selects BigTIFF only when the estimated output size requires it.

The production COG conversion completed for both corrected rasters. Each
output is tiled in 512 px blocks and contains overviews at factors 2 through
128. The validated RGB COG is 2,029,582,447 bytes and the float32 height COG
is 7,866,893,939 bytes. Both retain the 64,112 by 50,994 dimensions,
`EPSG:3947` CRS and 0.02 m pixel size; the height product retains NaN as its
NoData value. Full-resolution window reads at the corners and centre plus a
1:128 overview read completed successfully.

## GCP input limitation

`gcpPositionsXYZ_EPSG21781.csv` contains nine surveyed XYZ coordinates in
CH1903 / LV03 (`EPSG:21781`). The PNG files under `inputs/gcp_overview` are
annotated identification aids. They do not specify the source JPG name and
pixel coordinates required by DroneAI's OpenDroneMap-compatible
`gcp_list.txt` contract.

The supplied `example_quarry_2.p4d` project repeats the nine surveyed
coordinates and their 2 cm XY/Z accuracy, but contains no image-space GCP
marks. It therefore cannot supply the missing observations automatically.

The points must be marked in multiple original images and exported as
`gcp_list.txt` before weighted GCP adjustment can be enabled. The overview
PNGs must not be treated as surveyed image observations automatically.
