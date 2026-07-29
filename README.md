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
7. Tile detections are deduplicated and rendered on an annotated GeoTIFF.

The repository supports two execution modes:

- **local pipeline**: one resumable orchestrator, Docker, and no Kafka,
  PostgreSQL, MinIO, Kubernetes, or dashboard;
- **distributed pipeline**: five application services deployed by the Helm
  chart with Kafka, S3-compatible storage, and PostgreSQL/PostGIS.

The local pipeline is the fastest way to exercise the code. The distributed
stack is useful for studying service boundaries and delivery semantics, but it
is substantially heavier.

## Services

| Directory | Responsibility |
|---|---|
| `app1-colmap` | COLMAP reconstruction, GPS alignment, Gaussian training, orthomosaic generation |
| `app2-ia` | Tile-level YOLO OBB or SAM 3 inference |
| `app3-processing` | Tiling, overlap deduplication, detection persistence and annotated GeoTIFF output |
| `app4-dashboard/api` | FastAPI control plane, S3 dataset API, status consumer, transactional inbox/outbox |
| `app4-dashboard/frontend` | Next.js operator dashboard |
| `shared` | Configuration, persistence, storage, event contracts, delivery helpers and validation |
| `tools` | Infrastructure-free local runners and diagnostics |

For implementation details, read:

- [`DOCUMENTATION.md`](DOCUMENTATION.md) for architecture, event contracts,
  state and processing algorithms;
- [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md) for the infrastructure-free
  workflow;
- [`DEVELOPMENT.md`](DEVELOPMENT.md) for tests, linting and dependency locks;
- [`docs/FAST_ALIGNMENT.md`](docs/FAST_ALIGNMENT.md) for the sub-hour
  COLMAP/GLOMAP and RTK alignment path;
- [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) for the
  supported deployment boundary, required secrets and measured release gates;
- [`CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md`](CLOUD_DEPLOYMENT_OVHCLOUD_K3S.md) for
  the experimental Helm/K3s cloud path.

## Showcase

![Vehicle detection on orthomosaic](docs/showcase_vehicle_detection.png)

The example shows georeferenced vehicle detections reprojected onto an
orthomosaic. See
[`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md) for the measured
results of the local, non-RTK validation run.

## Quick start: local pipeline

The unified entry point runs COLMAP, Gaussian orthophoto generation and YOLO
OBB detection in order:

```bash
./tools/run_local_pipeline.sh /path/to/drone/photos /path/to/workspace \
  --profile standard
```

Use `--profile smoke` for a smaller 25-image validation. Existing outputs are
validated before a stage is skipped. Forced upstream rebuilds propagate to
dependent stages, and the orchestrator writes `pipeline_run.json` plus one log
per stage.

Useful controls:

```bash
# Inspect the planned commands without running them
./tools/run_local_pipeline.sh DATASET WORKSPACE --profile standard --dry-run

# Rebuild Gaussian outputs and the dependent detection stage
./tools/run_local_pipeline.sh DATASET WORKSPACE \
  --profile standard --from-stage gaussian --force-stage gaussian

# Run only detection when its prerequisites already exist
./tools/run_local_pipeline.sh DATASET WORKSPACE \
  --profile standard --from-stage detection
```

The shell entry point itself only needs Python 3.11 or 3.12, but its stages
launch Docker images:

| Stage | Required local image |
|---|---|
| dataset preflight | `droneai-api:local` |
| COLMAP | `drone-colmap:latest` |
| Gaussian | `droneai-gaussian-local:latest` |
| detection | `drone-ia:latest` |

GPU-backed stages require Docker, the NVIDIA Container Toolkit and a compatible
NVIDIA driver. Build the lightweight preflight image with:

```bash
docker build -f app4-dashboard/api/Dockerfile -t droneai-api:local .
```

The heavier image preparation and stage-specific commands are documented in
[`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md).

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

### Host requirements

Use Linux or WSL2 Ubuntu with:

- Docker and K3s;
- Helm;
- an NVIDIA GPU, driver and NVIDIA Container Toolkit for the GPU workers;
- outbound access for system packages, images and source dependencies;
- enough memory and disk for the COLMAP/DroneGS build and mission data.

The current local chart requests 80 GiB for the COLMAP worker and one GPU for
both COLMAP and IA. Adjust
[`charts/drone-ai/values.yaml`](charts/drone-ai/values.yaml) to match the host.
The first base-image build can take tens of minutes and consume tens of
gigabytes of transient build cache.

### External build sources

The COLMAP base image copies external source trees that are intentionally
ignored by Git. Prepare them once:

```bash
bash setup_deps.sh
```

`setup_deps.sh` prepares the default COLMAP dependencies:

| Directory | Source/version |
|---|---|
| `app1-colmap/ceres-solver/` | commit `849f854ff98f…`, Ceres 2.3-dev with cuDSS |
| `app1-colmap/colmap-local/` | tag `4.1.1`, minimal-pipeline patch applied |
| `app1-colmap/colmap-deps/` | SHA-addressed PoseLib and faiss archives |

ONNX Runtime GPU and the ALIKED/LightGlue model files are also checksum
verified by `setup_deps.sh`; production workers do not download them at
runtime.

LichtFeld and its vcpkg toolchain are not build dependencies. The exact
historical upstream revision used to derive GPL-covered DroneGS components is
recorded in
[`docs/dronegs/GPL_COMPONENTS.md`](docs/dronegs/GPL_COMPONENTS.md); this
provenance record is intentionally retained.

### Install and deploy

The automated setup installs the local dependencies and then deploys the Helm
release:

```bash
bash setup.sh
```

For an already prepared host:

```bash
bash setup_deps.sh

export HF_TOKEN=your_huggingface_token
sudo kubectl create namespace drone-ai --dry-run=client -o yaml \
  | sudo kubectl apply -f -
sudo kubectl -n drone-ai create secret generic hf-token \
  --from-literal=HF_TOKEN="$HF_TOKEN"

bash build_and_deploy.sh
```

The `hf-token` secret is currently required by the IA deployment even if only
YOLO will be selected. SAM 3 additionally requires approved access to the
gated `facebook/sam3` distribution.

`build_and_deploy.sh`:

1. checks the external sources and `hf-token`;
2. builds the COLMAP base when needed and all five service images;
3. imports the images into K3s containerd;
4. runs `helm upgrade --install` for `charts/drone-ai`;
5. waits for the `drone-ai` release in namespace `drone-ai`.

Use `bash build_and_deploy.sh --base` for a no-cache rebuild.

The chart deploys Kafka, MinIO, PostgreSQL/PostGIS, the five services, RBAC,
storage secrets and schema initialization.

For a reviewed single-tenant production deployment, start from
[`charts/drone-ai/values-production.example.yaml`](charts/drone-ai/values-production.example.yaml),
replace its example hosts and pre-create the referenced storage, API-key and
TLS secrets. The frontend receives the public API origin at runtime, prompts
the operator for a provisioned key and exchanges it for an eight-hour signed
HttpOnly cookie. The key is never compiled into the browser bundle or stored
in that cookie. The auth Secret must provide both `api-keys.json` and an
independent `session-secret`.

### Verify and access

```bash
kubectl get pods -n drone-ai
kubectl get svc -n drone-ai
kubectl logs deployment/kafka-broker -n drone-ai
kubectl logs deployment/dashboard-api -n drone-ai
```

With the default local values:

- dashboard: `http://localhost:30000`;
- API: `http://localhost:30080`;
- MinIO console: `http://localhost:30090`;
- MinIO API: `http://localhost:30091`;
- in-cluster Kafka:
  `my-kafka.drone-ai.svc.cluster.local:9092`.

Upload a dataset from the dashboard or through `POST /datasets/upload`, then
submit a mission referencing its normalized `datasets/<name>` S3 prefix.
Select a configured work drive, a COLMAP profile and either YOLO OBB or SAM 3.

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
| `GET` | `/pods` | Kubernetes pod status |
| `GET` | `/browse` | browse S3 prefixes |
| `GET` | `/datasets` | list datasets |
| `POST` | `/datasets/upload` | upload a dataset batch |
| `POST` | `/datasets/upload-file` | upload one dataset file |
| `DELETE` | `/datasets/{name}` | delete a dataset |
| `GET` | `/preview/{s3_key}` | render a bounded image preview |
| `GET` | `/maps/{vol_id}/metadata/{layer}` | inspect COG map metadata |
| `GET` | `/maps/{vol_id}/tiles/{layer}/{z}/{x}/{y}.png` | render one COG map tile |
| `GET` | `/maps/{vol_id}/vectors.geojson` | query AI vectors in a WGS84 bbox |
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
- `0002_inbox_outbox.py`.
- `0003_geospatial_aggregation.py`.

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
