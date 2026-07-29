# Development guide

DroneAI is an exploratory pipeline. Its distributed path has at-least-once
delivery primitives but is not an exactly-once or high-availability system.
The lightweight checks in this guide do not require Kafka, S3, Postgres,
Kubernetes, or a CUDA GPU.

## Supported toolchain

- Python 3.11 or 3.12
- Node.js 20
- npm with the committed `package-lock.json`

## Python environment

On Ubuntu:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --requirement requirements/dev.txt
```

Run the checks:

```bash
make compile
make lint
make test
```

The tests include architecture checks for the API composition root, public
route inventory, shared worker messaging, versioned event contracts, retry and
dead-letter behavior, transactional inbox/outbox rollback and retry using
SQLite, local orchestrator resumability, GeoPackage metadata and WGS84-to-EPSG
vector reprojection. When Fiona/GDAL is installed, the QGIS export tests also
open the generated GeoPackage through the GDAL driver and verify its layer CRS.

GPU and external-service tests are excluded from the default test command:

```bash
pytest -m gpu
pytest -m integration
```

`tools/smoke_cupy_ortho.py` is a manual diagnostic script. It requires a CUDA
GPU and mission-specific reconstruction artifacts and is not part of CI.

The infrastructure-free dataset and sparse reconstruction workflow is
documented in [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md).

## Dependency locks

The `.in` files under `requirements/` list direct dependencies. Regenerate the
corresponding lock after intentionally changing one of them:

```bash
python -m piptools compile requirements/api.in
python -m piptools compile requirements/processing.in
python -m piptools compile requirements/colmap.in
python -m piptools compile requirements/dev.in
```

Use the Python version of the corresponding runtime image when regenerating a
service lock.

`requirements/ia-extra.txt` is intentionally a direct-dependency lock layered
on top of the Ultralytics base image. Do not resolve and pin its transitive
Torch, CUDA, or NVIDIA dependencies independently from that image.

## Frontend

```bash
cd app4-dashboard/frontend
npm ci
npm run lint
npm run build
```

The lock currently pins Next.js `16.2.12`. Security advisories change over
time, so verify the current dependency graph locally:

```bash
npm audit --omit=dev
```

Review proposed major-version or forced changes before applying them; do not
use `npm audit fix --force` as an unreviewed lock-file rewrite.

## Full pipeline

The supported end-to-end entry point installs or validates runtime
dependencies, prepares external sources, builds every image and deploys the
dashboard:

```bash
./deploy.sh local
./deploy.sh distributed
```

Use `./deploy.sh <mode> --no-build` while iterating on runtime configuration.
See [`DEPLOYMENT.md`](DEPLOYMENT.md) for lifecycle and troubleshooting.
