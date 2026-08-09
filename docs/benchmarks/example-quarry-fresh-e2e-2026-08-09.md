# Example Quarry 2.0 fresh full E2E — 2026-08-09

## Verdict

The fresh release-qualification run completed successfully on BIGZEN from a
new direct multipart upload through COLMAP, DroneGS, COG publication, tiling,
YOLO inference and durable aggregation. The committed 5 m projected Gaussian
scale limit fixed the sparse-product regression seen in the previous run:
87.6% of the trained model was retained and the spatial coverage gate accepted
the 2 cm/px product with 99.9% valid pixels.

The run also exposed two local-stack integration issues. MinIO Community does
not implement bucket-level `mc cors set`; Compose and Helm now use its global
CORS environment contract. After a WSL restart and a Kafka session timeout,
the IA tile consumer also remained unassigned until its container was restarted.
The durable offsets preserved every tile, and a shared assignment watchdog was
added after the run so all work consumers recreate themselves after a sustained
loss of group assignment.

## Qualified environment

| Item | Value |
| --- | --- |
| Execution server | BIGZEN, Windows 10 host with Ubuntu WSL2 |
| GPU | NVIDIA GeForce RTX 3090, 24,576 MiB VRAM |
| WSL memory | 94 GiB visible, no swap |
| Ubuntu filesystem | 226 GiB; 118 GiB free before the run, 103 GiB after it |
| Runtime | Docker Compose, PostgreSQL/PostGIS, Kafka, MinIO, three workers, API and frontend |
| Application source | `5fb2e05` plus Compose/Helm CORS fixes from `169113c` |
| Dataset | `example_quarry_2.0`, 347 JPEG images, 3,011,669,069 bytes |
| Upload session | `99c76db6-2ec2-4c89-b6b2-58c1c4b2d5d9` |
| Dataset prefix | `datasets/example_quarry_2_0_fresh_20260809` |
| Mission ID | `example-quarry-2-fresh-e2e-20260809` |
| Product CRS | `EPSG:3947` |
| GCP policy | Disabled: surveyed coordinates exist, but image-space observations do not |

BIGZEN remained an execution mirror. All source changes and commits were made
in the authoritative local WSL repository. The application image rebuild reused
the existing CUDA/COLMAP base image; no CUDA or COLMAP source rebuild occurred.

## Direct ingestion and CORS

The dataset was uploaded under a new prefix through the durable API upload
session contract. All 347 files were initialized as multipart objects, uploaded,
completed and sealed by `dataset-manifest.json`. The 3.01 GB local transfer took
18.6 seconds with four concurrent upload workers.

The real `OPTIONS` preflight returned HTTP 204 with the requested `PUT` method,
origin and headers. Every actual part response contained an `ETag`,
`Access-Control-Allow-Origin: http://localhost:3000`, and an exposure policy
that makes `ETag` browser-readable.

The first local upload attempt also documented a BIGZEN-specific network
constraint: a process inside WSL must not hairpin through the Windows LAN
address used in browser-facing presigned URLs. The E2E uploader connected to
MinIO on `127.0.0.1` while retaining the signed public `Host` header. This did
not alter the API, signature or browser contract.

## Pipeline results

The mission ran from `2026-08-08 23:39:26 UTC` to
`2026-08-09 00:22:49 UTC`, an elapsed time of 43 minutes 23 seconds including
the IA consumer diagnosis and targeted restart.

| Stage | Result |
| --- | --- |
| Input preparation | 347/347 images copied; 347/347 EXIF positions extracted |
| SIFT extraction | GPU, 2.103 minutes |
| GPS-pair matching | 7,073 bounded pairs |
| Global mapping | 345/347 registered (99.4%); 76,367 points; 89.3 seconds |
| Undistortion | 345 images; 4.740 minutes |
| Geographic alignment | Mean reference error 0.887 m; transform scale 88.0558 |
| DroneGS training | 15,000 iterations; 1.5 M Gaussians; 649.1 seconds wall time |
| Gaussian qualification | PSNR 22.2686, SSIM 0.6082; gates 18.0 and 0.25 |
| Gaussian filtering | 1,314,390/1,500,000 retained (87.6%); gate 80% |
| Spatial coverage | 99.895% valid; 100% covered cells; worst cell 92.475%; accepted |
| Raster product | 63,466 x 50,959 at 0.02 m/px in `EPSG:3947` |
| Tiling | 5,412/5,412 tiles |
| IA and aggregation | 5,412/5,412 durable receipts; zero detections; completed |

Zero detections verifies inference and aggregation execution only. The quarry
dataset does not contain detection ground truth, so this is not an accuracy
claim for the selected `car` class.

## Published artifacts

| Product | Size | SHA-256 |
| --- | ---: | --- |
| RGB orthomosaic COG | 2,169,889,164 bytes | `1c32cc28a0f017e9cd0d6685dd8fbff31273f5b53d7145d0d0530b46b7bc2bcd` |
| DSM COG | 8,010,986,985 bytes | `255ffb1d00f1dd31835cc5fd6f73d595cc9626954d29e104a1e4074ed385c037` |
| Filtered Gaussian PLY | 310,197,518 bytes | `feec740756d17f3f537d053a91447d8aafbb8bd238085a986c7899c38fa1c981` |
| RGB preview | 418,002 bytes | `4f967c3cb95bd184feea6e66387c2c60f42c139eb03114bf5cd35e378bafe4aa` |
| DSM preview | 105,278 bytes | `aec81fadc6fb0c6eae2daafce769425a912775496b97731cbb65ea883ab636a4` |

The products are stored under
`drone-ai/missions/example-quarry-2-fresh-e2e-20260809/`. The product manifest,
coverage report, alignment report, RTK prior report, COLMAP models, training
manifest and held-out qualification manifest were published and size-verified.

## Independent COG reads

Both objects were reopened directly from MinIO through GDAL's S3 virtual
filesystem after publication. The checks verified:

- RGB: three `uint8` bands; DSM: one `float32` band with NaN NoData;
- `EPSG:3947`, 512 x 512 internal blocks and overviews 2 through 128;
- full-resolution 256 x 256 reads at both corners and the centre;
- a whole-raster 1:128 overview read for both products;
- expected NaN DSM values outside the mapped footprint and finite centre data.

## Kafka recovery observation

At the end of tiling, Kafka contained all 5,412 `image-tiles` records, but the
`ia-tile-workers` group had no active member after an earlier broker session
timeout during heavy COG I/O. Restarting only `ia-worker` restored the group at
the original offsets. The backlog reached zero, all 5,412 tile result objects
were published, and PostgreSQL ended at:

```text
status=success step=DONE progress=100
total_tiles=5412 tiles_received=5412 aggregation_status=completed
```

No record entered `pipeline-dead-letter`. The post-run fix adds a 60-second
unassigned-consumer watchdog shared by COLMAP, IA and Processing workers. It
closes and recreates only the affected consumer, leaving the durable inbox,
outbox and committed Kafka offsets authoritative.

## Automated verification

- Python: 565 passed; 13 CuPy-only tests skipped in the non-CUDA development environment;
- focused Kafka, worker messaging, dispatcher, partitioning and modular-boundary tests passed;
- Ruff passed on every changed Python file;
- the shared recovery primitive passed strict mypy checking;
- Compose configuration, Helm lint and Helm rendering passed for the MinIO CORS fix;
- real RTX 3090 SIFT, matching, global mapping, DroneGS, orthographic rendering and YOLO execution passed;
- CORS preflight, multipart completion, manifests, COG metadata and independent raster reads passed.
