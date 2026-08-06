# Development guide

DroneAI is an exploratory pipeline. Its distributed path has at-least-once
delivery primitives but is not an exactly-once or high-availability system.
The lightweight checks in this guide do not require Kafka, S3, Postgres,
Kubernetes, or a CUDA GPU.

## Supported toolchain

- Python 3.11 or 3.12
- GNU Make
- Node.js 20
- npm with the committed `package-lock.json`

## Python environment

On Ubuntu:

```bash
./scripts/bootstrap-dev.sh
source .venv/bin/activate
```

The bootstrap is idempotent. It installs missing native development tools on
APT-based systems, creates or refreshes `.venv` from the committed development
lock and runs the shared static checks. Set `PYTHON_BIN` to select a supported
Python interpreter explicitly.

Run the checks:

```bash
make static
make coverage
```

`make static` compiles Python sources, applies the repository and focused
worker lint rules, runs strict worker type checking, validates shell scripts
and GitHub Actions workflows, and rejects broken local Markdown links.
`make audit` checks the locked Python environment against published
vulnerability advisories. `make check` runs the static checks, dependency
audit and coverage suite, using the same commands enforced by CI. The
development lock installs the `actionlint` executable through `actionlint-py`,
with deterministic ShellCheck and Pyflakes integrations.

Pull-request CI classifies changed paths before starting expensive jobs. Native
DroneGS compilation, the dual-version Python suite, PostGIS migration
round-trips, frontend/Playwright checks, service image builds and Helm renders
only run when their own runtime or contract changes. Markdown-only pull
requests run the lightweight link contract. Changes to the CI workflow or its
scope selector deliberately run every job. Pushes to `main` and manual
dispatches retain the complete suite, so path filtering cannot replace the
post-merge integration gate. The selector and its regression tests live in
`scripts/ci/select_ci_jobs.py` and `tests/test_ci_change_scopes.py`.

Coverage uses branch measurement across the
application and local tools, with a repository-wide non-regression floor of
50%. That floor is a ratchet, not a completeness claim: new or changed pure
logic is expected to receive focused unit tests even when subprocess, CUDA or
external-service boundaries require integration tests.

The tests include architecture checks for the API composition root, public
route inventory, shared worker messaging, versioned event contracts, retry and
dead-letter behavior, transactional inbox/outbox rollback and retry using
SQLite, local orchestrator resumability, GeoPackage metadata and WGS84-to-EPSG
vector reprojection. When Fiona/GDAL is installed, the QGIS export tests also
open the generated GeoPackage through the GDAL driver and verify its layer CRS.

CI also rejects unused imports/locals and any new function above the repository
complexity budget across every Python service, shared module and local tool. The
local sparse runner, Gaussian orthophoto generator and production COLMAP worker
are composed from focused stages with typed, immutable state objects. Keep their
public entry points limited to stage coordination and add new behavior to the
smallest relevant stage. The COLMAP worker package additionally enforces modern
Bugbear/simplification/upgrade/Ruff/async rules and a McCabe ceiling of 15
across the complete worker package. The same modern rules cover `shared/`, with
an initial McCabe ceiling of 18; scientific Unicode such as sigma remains
allowed in operator-facing validation messages. Stable contracts, runtime
boundaries, artifact helpers, mission coordination and every COLMAP stage also
pass strict mypy checks. The same strict contract covers all 26 modules at the root of
`shared/`, including the SQLAlchemy, transactional inbox/outbox and S3
boundaries. Those dynamic integrations expose explicit session and S3 client
contracts while keeping runtime-generated ORM/client behavior behind the
boundary. Imports are skipped so missing or changing third-party stubs cannot
weaken either strict contract.
`tests/test_modular_boundaries.py` prevents the entry point and focused modules
from growing back into an orchestrator monolith.
Focused worker tests also exercise RTK candidate acceptance, rejection, cache
reuse and bounded fallback, plus mandatory publication assets, GCP provenance,
best-effort recovery uploads and aerial/facade completion routing.

GPU and external-service tests are excluded from the default test command:

```bash
pytest -m gpu
pytest -m integration
```

`tools/smoke_cupy_ortho.py` is a manual diagnostic script. It requires a CUDA
GPU and mission-specific reconstruction artifacts and is not part of CI.

CUDA container validation is split deliberately. The hosted
`cuda-containers.yml` workflow builds the development image, compiles a
portable DroneGS binary inside it, and builds the `dronegs-builder` stages from
both production Dockerfiles. A parallel matrix prepares the pinned external
COLMAP dependencies, builds both final CUDA runtime images, emits their Syft
CycloneDX and Trivy HIGH/CRITICAL evidence, and rejects fixable CRITICAL
findings. These hosted jobs validate Docker recipes and toolchains without
claiming to exercise a GPU. Pull requests and merges may start the lightweight
CUDA selector when relevant files change, but do not run either costly build
job unless the diff changes an authoritative `FROM nvidia/cuda:...` line or
the pinned `COLMAP_TAG` in `setup_deps.sh`. A manual `workflow_dispatch` is the
only override for explicitly requested rebuilds after other CUDA, COLMAP,
Dockerfile or validation changes. The
scheduled or manually dispatched
`dronegs-gpu-nightly.yml` workflow runs every native CUDA test inside the same
development container on a self-hosted runner, then verifies driver injection
in each production CUDA runtime image. It requires a repository runner labelled
`gpu` and `cuda` plus the repository variable `DRONEGS_GPU_CI=true`. The
workflow writes the commit, runner and result to the GitHub job summary and
retains the complete `gpu-validation.log` as a commit-scoped artifact for 30
days, including failed attempts. This artifact is the release evidence; a
successful local run against an uncommitted working tree is useful
qualification but does not replace the post-commit workflow result.

The GPU workflow exposes the available devices with Docker's `--gpus all` but
does not set a device index or `CUDA_VISIBLE_DEVICES`; CUDA and the NVIDIA
driver retain device selection. Run the same contracts locally with:

```bash
scripts/ci/validate_cuda_containers.sh build
scripts/ci/validate_cuda_containers.sh gpu
```

Both modes print the repository commit, Docker server version and CUDA image
contracts before building. The most recent local CUDA 12.9.2 qualification is
recorded in
[`docs/benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md`](docs/benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md).

The infrastructure-free dataset and sparse reconstruction workflow is
documented in [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md).

## Dependency locks

GitHub Actions are pinned to immutable commit SHAs and annotated with their
release major. Keep the SHA pin when updating them. Dependabot checks Actions,
Python, frontend npm and service Docker dependencies every Monday and groups
the Python, frontend and Actions updates to keep review volume bounded. Actions
using the Node.js 24 runtime require runner version 2.327.1 or newer; verify the
self-hosted GPU runner before enabling the nightly workflow.

The hosted CI builds the dashboard API, processing worker, CUDA COLMAP base and
local Gaussian runtime images, then generates a CycloneDX JSON SBOM with Syft
and a HIGH/CRITICAL JSON vulnerability report with Trivy for each image.
Fixable CRITICAL findings fail the image job; unfixed findings remain visible
in the report without making a release impossible. The commit-scoped
`supply-chain-<image>-<sha>` artifacts are retained for 30 days, including
failed jobs. Syft and Trivy container tags and multi-architecture digests are
pinned in `.github/workflows/ci.yml` and
`.github/workflows/cuda-containers.yml`.

The `.in` files under `requirements/` list direct dependencies. Regenerate the
corresponding lock after intentionally changing one of them:

```bash
python -m piptools compile requirements/api.in
python -m piptools compile requirements/processing.in
python -m piptools compile requirements/colmap.in
python -m piptools compile --allow-unsafe requirements/dev.in
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
npm run duplication
npm run test
npm run lint
npm run build
npx playwright install chromium
npm run test:e2e
```

From the repository root, `make frontend-e2e` builds the production Next.js
application and runs the same Playwright suite. The browser tests mock API
transport while exercising Chromium against the production build. The six
journeys cover dataset selection and mission launch, operator cancellation,
rendering of the terminal `cancelled` state, renewal of an expired browser
session, WebSocket reconnection with delivery of the recovered live event, and
projected GeoPackage export from a completed mission. CI installs Chromium with
its Linux system dependencies and uploads the Playwright report when the suite
fails.

The lock currently pins Next.js `16.2.12`. Security advisories change over
time, so verify the current dependency graph locally:

```bash
npm audit --omit=dev
```

Review proposed major-version or forced changes before applying them; do not
use `npm audit fix --force` as an unreviewed lock-file rewrite.

## Full pipeline

Product profiles must remain centralized. A facade parameter change belongs in
`shared/facade_process.py`; the API publishes that catalog and the frontend
consumes it, so values must not be copied into a React preset. Keep map defaults
in `shared/pipeline_params.py` and DroneGS-wide defaults in
`shared/dronegs_profile.py`.

Before changing the facade process, run at least:

```bash
PYTHONPATH=. python3 -m pytest -q \
  tests/test_facade_orthophoto.py \
  tests/test_local_colmap_runner.py \
  tests/test_validation.py
```

The facade tests cover the dashboard/API catalog, local-frame invariants,
detail-range audit, local runner parity and raster metadata. Product-quality
changes also require a dated qualification benchmark with the sparse
distribution, held-out PSNR/SSIM, loss evolution, iterations per second and
estimated/actual remaining time. Cahors is the current reference evidence, not
the identity or scope of the generic HD profile.

The supported end-to-end entry point installs or validates runtime
dependencies, prepares external sources, builds every image and deploys the
dashboard:

```bash
./deploy.sh local
./deploy.sh distributed
```

Use `./deploy.sh <mode> --no-build` while iterating on runtime configuration.
See [`DEPLOYMENT.md`](DEPLOYMENT.md) for lifecycle and troubleshooting.
