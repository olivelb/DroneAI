# DroneAI Pipeline

> [!IMPORTANT]
> DroneAI now provides an authenticated, role-separated production baseline
> for one organization behind TLS. It is not a public multi-tenant SaaS:
> identity federation, tenant ownership and object-prefix isolation remain
> separate requirements. The distributed workers retain at-least-once event
> delivery and are not an exactly-once or high-availability design.

DroneAI explores an end-to-end drone-image workflow:

1. COLMAP 4.1.1 reconstructs and geo-aligns a scene through a bounded
   GPS/temporal graph, GLOMAP and a compatible Caspar/Ceres fallback.
2. Corrected RTK/PPK missions receive one covariance-aware pose-prior
   refinement; standard GNSS missions skip it.
3. DroneGS trains a 3D Gaussian Splatting model through the immutable
   `DRONEGS_PRODUCTION_PROFILE_V1`.
4. CuPy renders a georeferenced orthomosaic and height map.
5. The orthomosaic is split into overlapping tiles.
6. Ultralytics YOLO OBB or Meta SAM 3 detects objects.
7. Tile detections are deduplicated, published as GeoJSON and optionally
   persisted as indexed PostGIS vectors.

The modern reconstruction default is the checkpoint-tested planimetric survey
profile (GPS pairs, SIFT CUDA at 2400 px, 4096 features, two GLOMAP BA passes
and final retriangulation). A separate `Precision 3D · RTK` preset uses
3200 px, 8192 features, guided matching and covariance-aware pose priors for
DSM/volume work; it is not the preferred planimetric preset. The dashboard
also exposes the measured 1600 px fast profile for large missions where
turnaround time is the priority.

The repository supports two execution modes:

- **local dashboard deployment**: Docker Compose with Kafka, MinIO,
  PostgreSQL/PostGIS, all workers and the complete dashboard;
- **distributed dashboard deployment**: the same application deployed to
  K3s by Helm with the NVIDIA device plugin;
- **infrastructure-free runner**: an advanced CLI for isolated scientific
  diagnostics without the dashboard.

## Services

| Directory | Responsibility |
|---|---|
| `app1-colmap` | COLMAP reconstruction, GPS alignment, Gaussian training, orthomosaic generation |
| `app2-ia` | Tile-level YOLO OBB or SAM 3 inference |
| `app3-processing` | Tiling, durable recovery, overlap deduplication and GeoJSON/PostGIS vector publication |
| `app4-dashboard/api` | FastAPI control plane, S3 dataset API, status consumer, transactional inbox/outbox |
| `app4-dashboard/frontend` | Next.js operator dashboard |
| `shared` | Configuration, persistence, storage, event contracts, delivery helpers and validation |
| `tools` | Infrastructure-free local runners and diagnostics |

For implementation details, read:

- [`DOCUMENTATION.md`](DOCUMENTATION.md) for architecture, event contracts,
  state and processing algorithms;
- [`DEPLOYMENT.md`](DEPLOYMENT.md) for the clone-to-dashboard local and
  distributed installation;
- [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md) for the infrastructure-free
  workflow;
- [`DEVELOPMENT.md`](DEVELOPMENT.md) for tests, linting and dependency locks;
- [`docs/FAST_ALIGNMENT.md`](docs/FAST_ALIGNMENT.md) for the sub-hour
  COLMAP/GLOMAP and RTK alignment path;
- [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) for the
  supported deployment boundary, required secrets and measured release gates;
- [`docs/benchmarks/helenenschacht-our-workflow-vs-metashape-2026-08-01.md`](docs/benchmarks/helenenschacht-our-workflow-vs-metashape-2026-08-01.md)
  for the independent GCP comparison between COLMAP/DroneGS and Metashape;
- [`docs/CONTRE_AUDIT_MERGE_C8D0464_2026-07-31.md`](docs/CONTRE_AUDIT_MERGE_C8D0464_2026-07-31.md)
  for the post-merge counter-audit and the resulting hardening work;
- [`CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md`](CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md) for
  the experimental Helm/K3s cloud path.

## Showcase

![Vehicle detection on orthomosaic](docs/showcase_vehicle_detection.png)

The example shows georeferenced vehicle detections reprojected onto an
orthomosaic. See
[`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md) for the measured
results of the local, non-RTK validation run.

## Measured geospatial accuracy

Helenenschacht provides 176 Autel XT705 RTK images and five surveyed targets
annotated in 35 source images. The targets are held out of pose, intrinsic and
reconstruction optimization and are read only after the products exist.

The selected `Precision 3D · RTK` run measured 6.320 cm horizontal, 15.741 cm
vertical and 16.962 cm 3D sparse checkpoint RMSE. Its final 5 mm DroneGS
orthomosaic preserved 6.24 cm horizontal RMSE when the five rendered target
centres were reconstructed, while the matching Metashape orthomosaic measured
14.88 cm. The final DroneGS DSM measured 11.44 cm vertical RMSE. Metashape was
denser in sparse tie points, but its current RTK-only project showed a 2.77 m
vertical bias at the checkpoints.

These are single-site results, not a universal accuracy claim. Output GSD is
sampling density rather than ground accuracy, Gaussian target centres can be
less certain when blurred or saturated, and only independent checkpoints can
validate a deliverable. The complete protocol, per-target coordinates,
uncertainty and weaknesses are documented in the
[comparative report](docs/benchmarks/helenenschacht-our-workflow-vs-metashape-2026-08-01.md)
and the earlier
[RTK/GeoTIFF A/B report](docs/benchmarks/helenenschacht-rtk-geotiff-ab-2026-07-31.md).

## Quick start: clone to dashboard

The recommended workstation deployment uses Docker Compose:

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI
./deploy.sh local
```

The production-like single-node Kubernetes deployment uses the same entry
point:

```bash
./deploy.sh distributed
```

Both commands prepare pinned external sources, build the five service images,
start Kafka, MinIO, PostGIS, migrations, workers and the dashboard, validate
the GPU runtime and print the effective dashboard link. `HF_TOKEN` is optional
for YOLO and required only for gated Hugging Face models such as SAM 3.

Use `--no-build` for a fast idempotent redeploy and `--base` for a full
no-cache rebuild. All options, prerequisites, persistence and operational
commands are documented in [`DEPLOYMENT.md`](DEPLOYMENT.md).

The dashboard-free diagnostic orchestrator remains available for focused
scientific tests:

```bash
./tools/run_local_pipeline.sh DATASET WORKSPACE --profile smoke
```

See [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md) for that advanced workflow.

DroneGS is the default Gaussian backend. The same frozen 172-view evaluator on
Albagnac measured 22.175919 dB PSNR, 0.642557 SSIM, and 0.325408 LPIPS in
972.731 seconds of training, versus 21.513821 dB, 0.586497, 0.371055, and
994.228 seconds for the historical deterministic LichtFeld control. DroneGS is
now the only executable Gaussian backend: the repository no longer clones,
builds, packages, launches, or dynamically selects LichtFeld. Versioned native
checkpoints resume interrupted jobs, and held-out PSNR/SSIM canaries gate the
model before downstream orthomosaic generation. Successful promotion discards
the large optimizer checkpoint, so it does not permanently duplicate the
final PLY state. The independent Savères 15k gate is documented in
[`phase4-saveres-checkpoint-canary-dev46-2026-07-27.md`](docs/dronegs/benchmarks/phase4-saveres-checkpoint-canary-dev46-2026-07-27.md).
The production V1 recipe was subsequently repeated five times on the complete
1,066-image SAVERES RTK scene: median training time was 607.1 seconds with
mean held-out PSNR 19.4122 dB and SSIM 0.49155. The complete machine-readable
record is
[`saleres-dronegs-production-v1-2026-07-28.json`](docs/benchmarks/saleres-dronegs-production-v1-2026-07-28.json).
An intentionally demanding Helenenschacht run at 5 mm/pixel took 1 h 46 min
44 s and failed the then-current 0.35 SSIM gate. Later full-scene evidence set
the current gate to 0.25 and made threshold-only re-evaluation reusable; its
[benchmark report](docs/benchmarks/helenenschacht-dronegs-ultra-5mm-2026-07-30.md)
documents why output GSD must not be confused with survey accuracy and why the
extreme recipe is not the new default.

## Distributed architecture

The distributed path uses the following event flow:

```text
Dashboard API --vols-bruts--> COLMAP worker
COLMAP worker --images-ortho--> processing worker
processing worker --image-tiles--> IA worker
IA worker --tile-detections--> processing worker
workers --pipeline-status--> Dashboard API --WebSocket--> frontend
Dashboard API --pipeline-control--> workers
```

Datasets and mission outputs use S3-compatible object storage:

```text
datasets/<dataset-name>/...
missions/<mission-id>/orthomosaic.tif
missions/<mission-id>/orthomosaic.tif.cog.json
missions/<mission-id>/orthomosaic.preview.webp
missions/<mission-id>/orthomosaic.height.tif
missions/<mission-id>/detections.geojson
missions/<mission-id>/analyses/<run-id>/detections.geojson
```

Orthomosaics and height maps are tiled Cloud Optimized GeoTIFFs (COGs) with
internal overviews. The dashboard reads only the windows required by the
current zoom, while AI detections remain a viewport-filtered vector overlay.
The full-screen Results workspace supports measurements, manual vector
editing, database search with automatic framing, rerunnable IA campaigns and
QGIS-ready downloads. Raster exports preserve their source CRS; GeoPackages
default to that same CRS and may instead use WGS84 or an operator-selected
EPSG code. RFC 7946 GeoJSON remains WGS84.
COLMAP downloads a selected `datasets/...` prefix into an ephemeral or
host-mounted work drive under `/work`, uploads durable outputs to S3, then
cleans its local mission workspace.

The dashboard API stores mission metadata, logs, detections, inbox receipts and
outbox rows in PostgreSQL/PostGIS. Mission creation, resume, cancellation and
status persistence use the transactional inbox/outbox boundary. GPU and
object-store worker outputs still use deterministic event IDs, explicit Kafka
flushes and manual commits; replay is therefore possible after a crash.
The complete audit remediation and geospatial delivery contract is documented
in [`AUDIT_COG_HARDENING_2026-07-29.md`](docs/AUDIT_COG_HARDENING_2026-07-29.md).
The rerunnable AI, spatial search, measurement and manual annotation contract
is documented in
[`GEOSPATIAL_WORKSPACE.md`](docs/GEOSPATIAL_WORKSPACE.md).

## Distributed local installation

Use the one-command deployment from a fresh clone:

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI
./deploy.sh distributed
```

The script installs or reuses K3s, Helm and the NVIDIA device plugin, prepares
the pinned external sources, builds and imports the five images, creates
portable persistent paths, sizes memory for the host, applies migrations and
waits for every deployment and browser endpoint.

The older `setup.sh` and `build_and_deploy.sh` names are compatibility wrappers
around this command. Manual dependency preparation, namespace creation,
RuntimeClass patching and `hf-token` creation are no longer required.

For YOLO, `HF_TOKEN` may remain unset. Export it before deployment only when a
gated Hugging Face model such as SAM 3 is needed. Use:

```bash
./deploy.sh distributed --no-build  # fast idempotent redeploy
./deploy.sh distributed --base      # complete no-cache rebuild
```

See [`DEPLOYMENT.md`](DEPLOYMENT.md) for host requirements, WSL systemd,
custom ports, persistence, lifecycle and the dashboard end-to-end procedure.

For a reviewed single-tenant production deployment, start from
[`charts/drone-ai/values-production.example.yaml`](charts/drone-ai/values-production.example.yaml),
replace its example hosts and pre-create the referenced storage, API-key and
TLS secrets. The frontend receives the public API origin at runtime, prompts
the operator for a provisioned key and exchanges it for an eight-hour signed
HttpOnly cookie. The key is never compiled into the browser bundle or stored
in that cookie. The auth Secret must provide both `api-keys.json` and an
independent `session-secret`.

The effective dashboard, API and MinIO URLs are printed after readiness.
Under native Ubuntu they normally use `localhost`; under WSL, distributed mode
prints the current WSL address because K3s NodePorts are not consistently
forwarded through Windows localhost.

### API surface

Current routes:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | API identity/health response |
| `POST` | `/auth/session` | exchange an API key for an HttpOnly browser session |
| `GET` | `/auth/session` | inspect the current browser principal |
| `DELETE` | `/auth/session` | clear the browser session |
| `POST` | `/mission` | persist and enqueue a mission |
| `POST` | `/mission/resume` | resume and enqueue a mission |
| `POST` | `/mission/cancel` | durably enqueue cancellation |
| `DELETE` | `/mission/{vol_id}` | remove mission S3 data and DB record |
| `GET` | `/mission/state` | inspect one mission |
| `GET` | `/mission/parameters` | pipeline metadata and work drives |
| `GET` | `/status/summary` | aggregate mission state |
| `GET` | `/pods` | Kubernetes pod or local Compose service inventory |
| `GET` | `/browse` | browse S3 prefixes |
| `GET` | `/datasets` | list datasets |
| `POST` | `/datasets/upload` | upload a dataset batch |
| `POST` | `/datasets/upload-file` | upload one dataset file |
| `DELETE` | `/datasets/{name}` | delete a dataset |
| `GET` | `/preview/{s3_key}` | render a bounded image preview |
| `GET` | `/maps/{vol_id}/metadata/{layer}` | inspect COG map metadata |
| `GET` | `/maps/{vol_id}/tiles/{layer}/{z}/{x}/{y}.png` | render one COG map tile |
| `GET` | `/maps/{vol_id}/vectors.geojson` | query AI vectors in a WGS84 bbox |
| `GET` | `/maps/{vol_id}/export/raster/{layer}` | stream a COG/GeoTIFF without buffering it in the API |
| `GET` | `/maps/{vol_id}/export/vectors` | export sources/annotations as GeoPackage or GeoJSON with CRS selection |
| `GET/POST` | `/maps/{vol_id}/analyses` | list or queue rerunnable COG AI campaigns |
| `POST` | `/maps/{vol_id}/analyses/{run_id}/retry` | retry a failed AI campaign |
| `POST` | `/maps/{vol_id}/analyses/{run_id}/cancel` | cancel an active AI campaign |
| `GET` | `/maps/{vol_id}/analyses/{run_id}/vectors.geojson` | query one persisted or object-store campaign |
| `GET` | `/maps/{vol_id}/search` | search indexed spatial features and return zoom bounds |
| `POST` | `/maps/{vol_id}/features` | create a tagged manual vector |
| `PATCH/DELETE` | `/maps/{vol_id}/features/{feature_id}` | update or remove a manual vector |
| `GET` | `/operations/outbox/dead` | list dead outbox events (admin) |
| `POST` | `/operations/outbox/{id}/replay` | replay a dead event (admin) |
| `GET` | `/files/{s3_key}` | redirect to a presigned S3 URL |
| `WS` | `/ws/status` | stream pipeline status |

## Incremental deployment

Each service script rebuilds one image, imports it into K3s, applies the Helm
release, and optionally restarts its deployment:

```bash
bash deploy_app1_colmap.sh
bash deploy_app2_ia.sh
bash deploy_app3_processing.sh
bash deploy_app4_api.sh
bash deploy_app4_frontend.sh
```

All accept `--base` for a no-cache build and `--no-restart` to stage an image
without restarting the workload. `deploy_app1_colmap.sh --base` also rebuilds
the COLMAP base image.

## Database schema

The repository contains Alembic migrations:

- `0001_initial_schema.py`;
- `0002_inbox_outbox.py`;
- `0003_geospatial_aggregation.py`;
- `0004_geospatial_workspace.py`;
- `0005_analysis_recovery_leases.py`.

For a manually managed database:

```bash
alembic upgrade head
```

The Helm `db-migrate-<revision>` job invokes `alembic upgrade head`.
Database-dependent pods use a schema-readiness init container, so they cannot
start before the target Alembic revision is active.

## Development checks

CPU-only checks do not require the distributed infrastructure:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements/dev.txt

make compile
make lint
make test
```

Frontend:

```bash
cd app4-dashboard/frontend
npm ci
npm run lint
npm run build
```

CI runs the same Python quality/tests and frontend lint/build jobs. GPU and
external-service tests are separately marked.

## Troubleshooting

### COLMAP base image is missing

```bash
bash deploy_app1_colmap.sh --base
```

### GPU is not visible in Kubernetes

```bash
nvidia-smi
sudo docker run --rm --gpus all \
  nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi
kubectl describe node | grep -A3 nvidia.com/gpu
kubectl describe pod -n drone-ai -l app=colmap
kubectl describe pod -n drone-ai -l app=ia
```

GPU indices inside containers are relative to `CUDA_VISIBLE_DEVICES`; with one
visible GPU, use index `0`.

### The dashboard has no data

```bash
kubectl get svc -n drone-ai
kubectl logs deployment/dashboard-api -n drone-ai
kubectl logs deployment/dashboard-frontend -n drone-ai
kubectl logs deployment/minio -n drone-ai
kubectl logs deployment/postgres -n drone-ai
```

### Local orchestrator refuses a workspace

The local runners only modify workspaces containing
`.droneai-local-workspace.json`. Source photographs must be in a separate
directory tree. See [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md) before using
`--force`.

## Build notes

`app1-colmap/Dockerfile.base` is a multi-stage CUDA 12.8.1 build:

- Ceres and COLMAP are compiled from source;
- Python dependencies come from `requirements/colmap.txt`;
- DroneGS is compiled locally in portable Turing-through-Blackwell mode;
- the matching DroneGS source and GPL provenance register ship with the
  runtime binary;
- the runtime uses `nvidia/cuda:12.8.1-base-ubuntu24.04` plus selected CUDA
  libraries.

COLMAP/Ceres currently target CUDA architectures `86-real;89-real` (Ampere and
Ada). DroneGS uses the portable CUDA build policy and detects the compatible
device code at runtime; its training profile has no per-architecture tuning.
Edit the COLMAP/Ceres architecture list before targeting another family for
reconstruction.

## Licensing

The repository source is licensed under the [MIT License](LICENSE). External
source trees downloaded by `setup_deps.sh`, container images, model code and
model weights keep their own licenses.

Important review points:

- COLMAP: BSD-3-Clause, with academic citations requested by upstream;
- Ceres Solver: BSD-3-Clause;
- DroneGS: original components use the repository license, while the combined
  MRNF/FastGS CUDA units and resulting native binary are distributed as
  GPL-3.0-or-later;
- LichtFeld-Studio: GPL-3.0-or-later, plus licenses of bundled dependencies;
- Ultralytics YOLO: AGPL-3.0 or a commercial enterprise license;
- Meta SAM 3 source and the gated `facebook/sam3` checkpoint: separate
  upstream terms, not covered by this repository’s MIT license;
- NVIDIA CUDA and NVIDIA container images: NVIDIA-specific terms;
- pretrained weights are separate artifacts and are not covered by the source
  license.

See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and the upstream
projects before redistributing images, binaries, models or datasets.

## Acknowledgements

DroneAI builds on COLMAP, PyCOLMAP, Ceres Solver, DroneGS, LichtFeld-Studio, CuPy,
NVIDIA CUDA, Ultralytics YOLO, Meta SAM 3, Hugging Face, Rasterio/GDAL,
PostgreSQL/PostGIS, MinIO, Apache Kafka, FastAPI, SQLAlchemy, Next.js, React,
Leaflet, Docker, K3s and Kubernetes.
