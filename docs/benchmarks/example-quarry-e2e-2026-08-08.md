# Example Quarry 2.0 full E2E — 2026-08-08

## Verdict

The production-shaped DroneAI pipeline completed successfully on BIGZEN from
dataset ingestion through COG publication, tiling, inference and detection
aggregation. The run exposed a product-quality regression that the status and
pre-filter view canary could not detect: the map-space Gaussian scale filter
removed 72.6% of the trained model and produced a sparse orthomosaic.

The regression was reproduced and corrected on the same qualified checkpoint.
The corrected downstream path — geo-alignment, filtering, 2 cm orthographic
rendering, BigTIFF writing and COG conversion — then completed successfully.
COLMAP and DroneGS training were deliberately not repeated for this controlled
comparison.

## Repository and execution roles

- `/home/olivier/droneAI` in the local Ubuntu WSL2 instance is the
  authoritative development repository.
- `/home/olivier/droneAI` on BIGZEN is an execution mirror used to build images
  and run CPU/GPU integration tests. It is not a second development repository
  and commits must not be created there.
- Before this E2E report was added, all 16 changed or newly created
  implementation, test and filter-benchmark files were compared between both
  checkouts by SHA-256 and were identical. Both checkouts were based on
  `6f120f927dc70f1a2059bd5bc71336251efeca75` on
  `codex/fix-gaussian-filter-coverage`.
- Dataset products and diagnostic rasters remain on BIGZEN; they are not copied
  into Git.

## Test environment

| Item | Value |
| --- | --- |
| Test server | BIGZEN, Windows 10 host with Ubuntu WSL2 |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB VRAM |
| WSL memory | 94 GiB visible, no swap |
| Container runtime | Docker Compose, NVIDIA runtime |
| Application services | PostgreSQL/PostGIS, Kafka, MinIO, dashboard API/frontend, COLMAP worker, processing worker, IA worker |
| Dataset | `example_quarry_2.0`, 347 JPEG images at 5,472 x 3,648 |
| Mission ID | `example-quarry-2-e2e-20260808` |
| Input object prefix | `datasets/example_quarry_2_0` |
| Product CRS | `EPSG:3947` |
| GCP policy | Disabled: surveyed coordinates were present but image-space observations were not |

The application images were built from the repository mirror. Rebuilds made
while diagnosing the result reused the existing CUDA/COLMAP base image; no
COLMAP or CUDA source build was triggered.

## Full E2E execution

The mission started at `2026-08-08 17:55:54 UTC` and reached final success at
`18:28:32 UTC`, for an elapsed time of 32 minutes 39 seconds.

| Stage | Observed interval | Result |
| --- | ---: | --- |
| Download and preparation | 9 s | 347/347 images copied |
| GPS/CRS extraction | <1 s | 347 EXIF positions, `EPSG:3947` selected |
| SIFT feature extraction | 2 min 11 s | GPU feature extraction completed |
| GPS-pair matching | 10 s | Completed |
| View-graph calibration | <1 s | Completed |
| Global mapping | 1 min 27 s | 344/347 images registered (99.1%) |
| Undistortion | 4 min 33 s | Dense/DroneGS input prepared |
| Geographic Sim(3) alignment | 1 s | Scale `88.2641245` m/model unit |
| DroneGS and orthographic product | 16 min 3 s | 15,000 iterations, 1.5 M Gaussians |
| COG conversion and publication | 3 min 14 s | RGB, DSM, previews, manifests uploaded |
| Tiling and IA | overlapped, 4 min 42 s | 4,620 tiles, aggregation completed |

DroneGS held-out qualification passed with `PSNR=22.0615` and `SSIM=0.6095`
against configured minima of 18 and 0.25. The IA pass produced zero detections.
That verifies inference and aggregation execution, but is not an accuracy claim
for the quarry dataset because it has no matching detection ground truth.

The mission database ended with `status=success`, `progress=100`, and the
aggregation status completed.

## Initially published artifacts

The original E2E mission used the former 1 m filter threshold and published
the following internally consistent but visually unacceptable products:

| Product | Size | SHA-256 |
| --- | ---: | --- |
| RGB orthomosaic COG | 1,101,477,430 bytes | `c552b7d0812018821c4b655ebd91e3f964f3e105ebc6af0f51947471dc665ae4` |
| DSM COG | 3,334,114,348 bytes | `97893b92e3f259fcc5e52f86ec49ddbb2d1734773224e0b6c2659be7646fd723` |
| Filtered Gaussian PLY | 96,850,685 bytes | `79aef392bdece763c4c3f7c726707b43f094942126c412d484366eb692c84555` |

The published RGB raster was 59,019 by 46,233 pixels at 0.02 m/px. Its
manifest records only 410,378 retained Gaussians, confirming that the sparse
appearance occurred after the held-out training canary.

The original published namespace remains
`drone-ai/missions/example-quarry-2-e2e-20260808/`. It was not overwritten by
diagnostics because doing so would invalidate its product-manifest hashes.

## Corrected downstream validation

Controlled 0.20 m/px renders of the same qualified 1.5 M-Gaussian checkpoint
showed that a 5 m maximum projected scale restored the useful footprint while
still rejecting the large blurred peripheral splats seen at 10 m and with the
filter disabled. The detailed measurements are in
[`example-quarry-gaussian-filter-coverage-2026-08-08.md`](example-quarry-gaussian-filter-coverage-2026-08-08.md).

The final corrected validation used:

- `gs_filter_max_scale=5.0` projected metres;
- `gs_filter_min_retained_ratio=0.80`;
- opacity threshold `0.005`;
- the same qualified checkpoint, without COLMAP or retraining.

It retained 1,358,194/1,500,000 Gaussians (90.5%) and produced RGB and height
rasters measuring 64,112 by 50,994 pixels at 0.02 m/px in `EPSG:3947`.

Both outputs exceeded or risked the classic TIFF 4 GiB limit. The writer now
uses `BIGTIFF=IF_SAFER`, after which the production COG conversion completed
with 512 px internal blocks and overview factors 2, 4, 8, 16, 32, 64 and 128.

| Corrected diagnostic product | Final COG size |
| --- | ---: |
| RGB | 2,029,582,447 bytes |
| DSM float32 | 7,866,893,939 bytes |

Random full-resolution reads at both corners and the centre, metadata reads,
and a 1:128 overview read completed for both COGs. The RGB contains three
`uint8` bands; the DSM contains one `float32` band and preserves NaN NoData.

The corrected products remain isolated under
`/home/olivier/droneai-data/colmap-work/diagnostics/example-quarry-2-e2e-20260808`
on BIGZEN. They are validation artifacts, not replacements for the published
mission products.

## Automated verification

- Python: 511 passed, 13 skipped because CuPy is unavailable in the local WSL
  test environment.
- Ruff: changed Python files passed.
- Frontend ESLint: passed.
- Frontend Vitest: 8 passed.
- CUDA integration: real RTX 3090 filtering and 2 cm rendering passed.
- Destructive-filter gate: the former 1 m result was rejected at 27.4%
  retention against the new 80% minimum.
- GeoTIFF/COG: BigTIFF signatures, dimensions, CRS, transforms, bands, NoData,
  tiles, overviews and representative reads passed.

## Remaining acceptance work

A fresh mission from ingestion through IA using the committed 5 m default is
the final release-level regression test. It is intentionally separate from
this controlled checkpoint reuse because repeating COLMAP and 15,000 DroneGS
iterations would not add information to the filter comparison itself.

For a GCP-qualified run, each surveyed point must first be marked in at least
two registered source images and exported through DroneAI's
OpenDroneMap-compatible `gcp_list.txt` contract. The supplied overview PNGs and
Pix4D project contain point positions and identification aids, but not those
image/pixel observations.
