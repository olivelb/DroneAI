# Villesèque P4 high-quality full E2E — 2026-08-09

## Verdict

The production-like pipeline completed successfully on BIGZEN from a fresh
multipart upload through GPU SIFT, bounded matching, GLOMAP, DroneGS, COG
publication, Kafka tiling, YOLO inference, SAM3 campaigns and durable spatial
aggregation. Runtime, recovery, provenance and artifact contracts are
qualified for this workload.

The generated map is **not** qualified as survey-grade or as a production AI
product. The GPS-only alignment differs from the Metashape reference by a few
metres, the DroneGS raster contains visible smearing despite passing its
held-out and coverage gates, YOLO found no vehicles on either orthomosaic, and
manual review found many false positives in the SAM3 results. These failures
are evidence from the benchmark, not pipeline crashes.

![Full DroneAI orthomosaic preview showing the Villesèque P4 quarry and the
visible local smearing](villeseque-p4-hq-ortho.webp)

*Full 1,978 x 2,048 WebP preview generated from the published RGB COG. The
source GeoTIFF is 32,438 x 33,582 pixels; this preview is documentation
evidence, not the measurement raster.*

## Qualified environment

| Item | Value |
| --- | --- |
| Execution server | BIGZEN, Windows 10 host with Ubuntu WSL2 |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB VRAM |
| WSL memory | 94 GiB visible, no swap |
| Ubuntu filesystem | 226 GiB; 72 GiB free after all campaigns |
| Runtime | Docker Compose, PostgreSQL/PostGIS, Kafka, MinIO, three workers, API and frontend |
| Application source | `5826f85` plus the local fixes documented below |
| Dataset | `P4`, 383 JPEG images, 3,337,582,470 bytes |
| Upload session | `bc4aed02-586c-414c-9beb-6beabaa26c6f` |
| Dataset prefix | `datasets/villeseque_p4_hq_20260809` |
| Mission ID | `villeseque-p4-hq-e2e-20260809` |
| Product CRS | `EPSG:3944` |
| Ground control | None; consumer GPS EXIF only |

BIGZEN remained an execution mirror. Source fixes were made in the
authoritative local WSL repository. Rebuilding the application images reused
the existing CUDA/COLMAP base image; no CUDA or COLMAP native build occurred.

## Input and reference products

The 383 source images were copied from the Windows SSD to ext4 before upload.
A SHA-256 manifest verified every copied file. Direct reads from the Windows
mount reached about 193 MB/s, but ext4 kept the long run independent of drvfs
latency.

The parent directory contains two useful Metashape references:

- `carriere villeseque ortho.tif`: 21,419 x 22,611, four `uint8` bands,
  `EPSG:4326`, about 2.35 cm ground sampling distance;
- `carrière DEM2.tif`: 27,197 x 27,197, one `float32` band, `EPSG:4326`,
  numeric elevation with `-32767` NoData.

The other three-band `DEM.tif` products are styled rasters, not numeric DEMs.
The available `plan.csv` is a flight plan rather than surveyed GCP image
observations, so GCP adjustment was correctly disabled.

## Direct ingestion

The dataset was uploaded through the real durable multipart API contract. All
383 files were initialized, uploaded, completed and sealed by a dataset
manifest. The 3.34 GB transfer took 22.17 seconds with four upload workers,
about 143.6 MiB/s, with zero failures.

The browser contract was also exercised through the Windows-to-WSL SSH tunnel.
MinIO returned HTTP 204 for the real CORS preflight and exposed multipart
`ETag` headers to the dashboard origin.

## High-quality profile

| Parameter | Value |
| --- | ---: |
| Feature image size | 4,096 px |
| SIFT features per image | 16,384 |
| Matches per pair | 32,768 |
| MVS/undistortion size | 4,096 px |
| DroneGS iterations | 30,000 |
| Maximum Gaussians | 3,000,000 |
| DroneGS source resize factor | 1 |
| DroneGS maximum width | 4,096 px |
| Checkpoint interval | 2,000 iterations |
| Orthomosaic/DSM resolution | 0.015 m/px |
| Gaussian maximum projected scale | 5 m |
| Minimum retained ratio | 80% |

## Pipeline results

The core run started at `2026-08-09 09:58:59 UTC` and retired its recovery
state at `11:53:54 UTC`, an elapsed time of 1 hour 54 minutes 55 seconds. The
initial YOLO aggregation completed at `11:56:42 UTC`.

| Stage | Result |
| --- | --- |
| Input preparation | 383/383 images copied; 383 positioned images |
| SIFT extraction | GPU, 2.832 minutes |
| Matching | 7,139 bounded GPS/temporal pairs; 1.561 minutes |
| Global mapping | 376/383 registered (98.2%); 486,144 points; registration gate passed |
| Undistortion | 376 images at 4,096 px; 4.941 minutes |
| GPS alignment | Mean 3.944 m; median 4.021 m; Sim3 scale 44.1687 |
| DroneGS training | 30,000 iterations; 5,399.7 seconds wall time; 2,706,676 final Gaussians |
| Held-out qualification | PSNR 23.9233, SSIM 0.5801; gates 18.0 and 0.25 passed |
| Gaussian filtering | 2,700,514/2,706,676 retained (99.8%); gate 80% passed |
| Spatial coverage | 100% valid; 100% covered cells; worst cell 100%; accepted |
| Raster product | 32,438 x 33,582 at 0.015 m/px in `EPSG:3944` |
| Tiling | 1,848/1,848 overlapping 1,024 px tiles |
| Initial YOLO | 1,848/1,848 receipts; zero detections; durable aggregation completed |

Training used 329 images and held out 47 images. GPU utilization stayed near
98–99% during training, with about 7.1 GiB VRAM and 11.4 GiB container memory.
The machine did not require the 90+ GiB RAM quotas that had been considered for
cloud GPU flavours.

## Published artifacts

| Product | Size | SHA-256 |
| --- | ---: | --- |
| RGB orthomosaic COG | 2,015,021,417 bytes | `67c96065c9912c9b8e41f6c8841f940ae078e6618bf0aa35348570de61a84be2` |
| DSM COG | 3,704,369,489 bytes | `fde08abd2801fc40e1dcfe3150f117edae3ee38d53c9685023b0cd7d820221a9` |
| Filtered Gaussian PLY | 637,322,782 bytes | `6bca36cb7ee1e47cb4c443b39615de6d0033e0431a0177aa84af2fd2e283720a` |
| RGB preview | 765,296 bytes | `6ba62769771530ab0a4cf6562911da2ee1a906fd36a97c7fe6d238c86f155d28` |
| DSM preview | 112,770 bytes | `e5e25ad3521e90e41ffc4753cc44c4a9e2d2b6ca68d8f2fbba6c08ee8e45ac5d` |

The products and their manifests are stored under
`drone-ai/missions/villeseque-p4-hq-e2e-20260809/`. Both COGs have 512 x 512
internal blocks and overviews 2 through 128.

## Metashape comparison

The DroneAI COGs and Metashape references were independently reprojected to a
0.25 m comparison grid in `EPSG:3944`. A gradient phase correlation estimated
that the Metashape image would move about 2.06 m east and 1.75 m south to align
with DroneAI. The phase response was only 0.047, so this horizontal estimate is
indicative rather than survey evidence. Gradient correlation improved from
0.178 to 0.197 after the shift.

| Elevation statistic | DroneAI DSM | Metashape DEM2 |
| --- | ---: | ---: |
| 2nd percentile | 175.21 m | 177.20 m |
| Median | 186.12 m | 189.73 m |
| 98th percentile | 219.42 m | 217.95 m |

Across 3,558,370 common comparison pixels, DroneAI minus Metashape had a
median bias of -3.20 m, MAE 4.21 m and RMSE 5.18 m. Removing only the median
vertical bias gave MAE 3.28 m, RMSE 4.21 m and NMAD 4.17 m.

These figures mix a Gaussian top-surface DSM with a Metashape DEM and may also
include acquisition/date differences. Without surveyed checkpoints they
measure cross-product agreement, not absolute accuracy.

## AI campaigns

Every production campaign processed and persisted all 1,848 tiles. The SAM3
identity was `facebook/sam3` revision
`3c879f39826c281e95690f02c7821c4de09afae7`, artifact SHA-256
`6d06f0a5f84e435071fe6603e61d0b4cc7b40e0d39d487cfd4d67d8cc11cc14a`,
with Torch 2.11.0+cu128, Transformers 5.14.1 and CUDA bfloat16 inference.

| Campaign | Durable result | Manual interpretation |
| --- | --- | --- |
| YOLO26l-OBB, `car` + `truck`, confidence 0.20 | 0 boxes | No vehicle found |
| SAM3 combined prompt, confidence 0.20 | 1 segment | False positive on conveyor |
| SAM3 `car`, confidence 0.20 | 44 raw masks, 27 after deduplication | About 7 plausible car masks and about 20 false positives; no labelled ground truth |
| SAM3 `construction vehicle`, confidence 0.20 | 2 segments | Both false positives: conveyor and a car |

The `car` run had median confidence 0.25; only four segments were at least 0.50
and one was at least 0.80. Counts must therefore not be treated as asset
counts without review.

Two controls isolated the detector and raster effects:

- YOLO26l-OBB returned zero boxes on 780 valid Metashape tiles at both 0.20
  and 0.10. The selected DOTA OBB checkpoint is not suitable for this vehicle
  inventory without adaptation.
- SAM3 `car` returned 52 raw masks on the Metashape reference. They grouped
  into 25 candidate locations within 4 m. At those same locations on DroneAI,
  targeted SAM3 found four masks in three regions; native review confirmed two
  true cars and two large false masks. The sharper reference helps SAM3, but
  prompt, deduplication and precision controls remain necessary.

## Integration findings and fixes

The run exposed three reproducibility issues:

1. BIGZEN's browser services are reachable through SSH port forwards, not the
   WSL LAN address. The qualified local endpoints were dashboard `30000`, API
   `30080`, MinIO console `30090` and MinIO S3 `30091`.
2. Compose allowed CORS only for dashboard port 3000. The MinIO origin list now
   also covers port 30000 and is configurable through
   `DRONEAI_MINIO_CORS_ALLOW_ORIGIN`.
3. A newly initialized Hugging Face named volume was owned by root while the
   IA image runs as UID/GID 10001. The image now creates
   `/cache/huggingface` with the runtime ownership before volume initialization.

The full CPU suite also exposed a clock-dependent watchdog test. The consumer
recreation helper now accepts the same optional monotonic timestamp used by the
watchdog test, removing dependence on WSL uptime.

## Qualification gaps

- Held-out PSNR/SSIM and coverage gates did not reject visible local smearing.
  A release gate for native-resolution sharpness/ghosting still needs a
  benchmark-backed threshold.
- Survey accuracy requires real GCP observations or RTK checkpoints. Consumer
  GPS alone cannot qualify centimetric products.
- Vehicle inventory requires a labelled aerial quarry evaluation set and a
  detector adapted to that domain. SAM3 output needs prompt-specific runs,
  confidence/size filtering and measured precision/recall.
- Concurrent analyses are durable but the single processing consumer serializes
  their expensive tiling phases. No event was lost, though one run's progress
  waited while the second tilter held the processing loop.

## Automated verification

- Real RTX 3090 CUDA SIFT, matching, GLOMAP, DroneGS training, rendering,
  YOLO26l-OBB and SAM3 inference passed at runtime.
- Multipart upload, manifests, CORS preflight, COG metadata, object
  verification, Kafka receipts and PostGIS feature persistence passed.
- Focused Kafka reliability, GPU dependency and production contract tests
  passed; Ruff and strict mypy passed on the touched modules.
- The complete CPU suite passed: 575 tests passed and 13 CUDA-only tests were
  skipped in the local non-CUDA development environment. The skipped GPU paths
  were exercised by the real RTX 3090 E2E run documented above.
