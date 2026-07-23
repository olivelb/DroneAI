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
dead-letter behavior, and local orchestrator resumability.

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

As of July 2026, `npm audit --omit=dev` still reports three high-severity
transitive advisories inherited through Next.js (`postcss` and `sharp`).
Next.js itself is pinned to the patched 16.2.11 release. Do not apply the
currently suggested `npm audit fix --force`: npm proposes an incompatible
Next.js downgrade. Reassess these advisories when upgrading Next.js.

## Full pipeline

The end-to-end pipeline additionally requires K3s, Docker, an NVIDIA GPU,
Kafka, MinIO, Postgres/PostGIS, and the external sources prepared by
`setup_deps.sh`. See `README.md` for that workflow.
