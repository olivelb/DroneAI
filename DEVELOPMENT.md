# Development guide

DroneAI is a preproduction geospatial engineering platform. Its distributed
path has at-least-once delivery primitives but is not an exactly-once or
high-availability system.
The lightweight checks in this guide do not require Kafka, S3, Postgres,
Kubernetes, or a CUDA GPU.

## Supported toolchain

- Python 3.12
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

The development lock pins `pip` 26.2.1 so the pinned `pip-tools` compiler can
regenerate every lock from its committed input file with an audited toolchain.

Run the checks:

```bash
make static
make coverage
```

Branch coverage must remain at or above 65%. The current audited suite reports
70%; increase the floor only with measured margin and critical-path tests.

`make static` compiles Python sources, applies the repository and focused
worker lint rules, runs strict worker type checking, validates shell scripts
and GitHub Actions workflows, and rejects broken local Markdown links.
`make audit` checks the locked Python environment against published
vulnerability advisories. `make check` runs the static checks, dependency
audit and coverage suite, using the same commands enforced by CI. The
development lock installs the `actionlint` executable through `actionlint-py`,
with deterministic ShellCheck and Pyflakes integrations.

Pull-request CI classifies changed paths before starting expensive jobs. Native
DroneGS compilation, the Python 3.12 quality and CPU-test suite, PostGIS migration
round-trips, frontend/Playwright checks, service image builds and Helm renders
only run when their own runtime or contract changes. Markdown-only pull
requests run the lightweight link contract. Changes to the CI workflow or its
scope selector deliberately run every PR job. Merges do not start another CI
run: protected `main` accepts changes through a previously validated PR.
Manual dispatch retains the complete suite for release qualification or an
explicit recheck. The selector and its regression tests live in
`scripts/ci/select_ci_jobs.py` and `tests/test_ci_change_scopes.py`. The always
present `CI gate` accepts successful or intentionally skipped scoped jobs and
fails for any failed or cancelled selected job. Branch protection requires
this aggregate check and `CUDA validation gate`. Split database model modules
and migration verifier changes also select the real PostgreSQL migration gate.

Coverage uses branch measurement across the
application and local tools, with a repository-wide non-regression floor of
60%. The current CPU suite measures 70%, leaving ten points of headroom.
That floor is a ratchet, not a completeness claim: new or changed pure
logic is expected to receive focused unit tests even when subprocess, CUDA or
external-service boundaries require integration tests.

The tests include architecture checks for the API composition root, public
route inventory, shared worker messaging, versioned Pydantic event contracts,
the generated discriminated JSON Schema, retry and
dead-letter behavior, transactional inbox/outbox rollback and retry using
SQLite, local orchestrator resumability, GeoPackage metadata and WGS84-to-EPSG
vector reprojection. When Fiona/GDAL is installed, the QGIS export tests also
open the generated GeoPackage through the GDAL driver and verify its layer CRS.
Named SQL `CHECK` constraints reject impossible mission, aggregation, analysis,
tile, map-feature, log, inbox and outbox states. Their SQLite regression tests
run in the default suite, while CI validates migration `0007` through the
PostgreSQL/PostGIS upgrade/downgrade/re-upgrade job.
Migration `0008` adds renewable leases to the shared worker inbox; broker-free
tests cover overlap deferral, heartbeat ownership, stale takeover, failure
reclaim and duplicate suppression without keeping a database transaction open
around long GPU, S3 or raster work.

CI also rejects unused imports/locals and any new function above the repository
complexity budget across every Python service, shared module and local tool. The
local sparse runner, Gaussian orthophoto generator and production COLMAP worker
are composed from focused stages with typed, immutable state objects. Keep their
public entry points limited to stage coordination and add new behavior to the
smallest relevant stage. The COLMAP worker package additionally enforces modern
Bugbear/simplification/upgrade/Ruff/async rules and a McCabe ceiling of 15
across the complete worker package. The same modern rules cover `shared/`, with
an initial McCabe ceiling of 18; scientific Unicode such as sigma remains
allowed in operator-facing validation messages. The service-core ratchet covers the current bounded detection runtime;
there is no app3 service or Kafka compute composition to maintain. Stable
contracts, runtime boundaries, artifact helpers, mission coordination and
every COLMAP stage pass strict mypy checks. The same strict contract covers
all 70 tracked Python modules at the root of `shared/`, including SQLAlchemy,
transactional control-plane inbox/outbox and S3 boundaries. Dynamic ORM and
client behavior remains behind explicit session and storage protocols; imports
are skipped so third-party stubs cannot silently weaken the local contracts.

The complete `app2-ia` bounded worker core passes the modern Ruff and strict
mypy gates. `stage_executor.py` owns the one-shot Stage Job boundary,
`detection_stage.py` and `detection_shard_stage.py` own raster inference and
receipt publication, `sam3_backend.py` owns lazy model loading and segmentation,
and `detection_core.py` owns typed records and overlap deduplication. Indexed
fan-out and CPU finalization reuse the persisted shard plan and verified
receipts. No runtime source remains under the retired `app3-processing` path.

The dashboard API strict-typing ratchet covers the package boundary,
RBAC/session security, Kafka/outbox publication, transactional status inbox
and WebSocket fan-out, Kubernetes status records and image preview helpers.
It also checks mission and map Pydantic schemas with their real framework
types, raster rate limiting, mission state/resume policy and geospatial
query/storage helpers. Explicit protocols and typed dictionaries keep legacy
SQLAlchemy queries and validated JSON at narrow dynamic boundaries. The first
strict route-adapter boundary covers browser authentication, dataset browsing
and batch upload, mission lifecycle/status and administrative outbox recovery.
The geospatial composition router, raster metadata, tile and combined vector
read paths, plus rerunnable analysis lifecycle and result publication are
covered too. Shared route protocols keep SQLAlchemy queries narrow without
weakening response contracts. Raster and QGIS-compatible vector exports are
covered as well, including streamed object cleanup. The feature-editing route
adapter now completes the strict route boundary, including typed search filters,
`FOR UPDATE` mutation records and the optimistic-version `409` invariant. Every
dashboard HTTP route module is therefore covered without broad ignores.
`tests/test_modular_boundaries.py` prevents the entry point and focused modules
from growing back into an orchestrator monolith.

Kafka event payloads are defined once in `shared/event_schemas.py`. The three
version-one event families (`status`, `control`, and `dead_letter`) share a trace
envelope and receive strict field-level validation while allowing additive
extension fields. Regenerate the machine-readable contract after changing a
model:

```bash
python tools/export_event_schemas.py
```

`make static` verifies that
`docs/contracts/kafka-events-v1.schema.json` is current.

Control and status events use tenant-scoped mission keys. Compute runs through
durable Stage Jobs and immutable manifests; Kafka compute contracts and
worker inbox leases have been removed. Helm topic definitions may increase
partition counts but never reduce them.

Current tests exercise RTK promotion/rejection, bounded reconstruction,
versioned workspace restore/publication, GCP provenance, coverage gates and
aerial/facade routing. Detection tests check immutable model identity, indexed
shard completeness and atomic artifact/feature publication.

GPU and external-service tests are excluded from the default test command:

```bash
pytest -m gpu
pytest -m integration
```

Frontend dependency installation is pinned by `packageManager` to npm 10.8.2.
Use `corepack npm` (the Makefile default) so local Node/npm upgrades cannot
rewrite a lockfile topology that differs from GitHub Actions; the frontend CI
uses the same Corepack-resolved package manager.

`tools/smoke_cupy_ortho.py` is a manual diagnostic script. It requires a CUDA
GPU and mission-specific reconstruction artifacts and is not part of CI.

CUDA container validation is split deliberately. The hosted
`cuda-containers.yml` workflow builds the development image, compiles a
portable DroneGS binary inside it, and builds the `dronegs-builder` stages from
both production Dockerfiles. A parallel matrix prepares the pinned external
COLMAP dependencies, builds both final CUDA runtime images, emits their Syft
CycloneDX and Trivy HIGH/CRITICAL evidence, and rejects fixable HIGH and CRITICAL
findings. These hosted jobs validate Docker recipes and toolchains without
claiming to exercise a GPU. Pull requests may start the lightweight CUDA
selector on every PR. Any CUDA Dockerfile, native source/header, CMake,
copied dependency/license, GPU lockfile, build harness or selection-policy
change invalidates the build evidence. The shared path classifier
`scripts/ci/cuda_change_scope.py` includes deletions and both sides of renames;
it does not depend on version-line changes. Unrelated application and prose
changes stay exempt. Merges do not start a second CUDA workflow; manual
`workflow_dispatch` explicitly selects all CUDA work.

Hosted CUDA build/SBOM selection covers the complete Docker build context.
Physical GPU selection is narrower: native DroneGS sources/tests/CMake, the
three CUDA Dockerfiles, the GPU harness and their selection contracts. Changes
to copied licences, COLMAP dependency sources or Python lockfiles rebuild and
scan the images but do not consume a GPU runner because the native GPU suite
does not exercise them. Unknown events still select both paths.

The always-present `CUDA validation gate` and `GPU qualification gate`
require a successful selector and an explicit boolean decision. Selected jobs
must succeed; `skipped` is valid only for an explicit `false` selection.
The general `CI gate` enforces the same contract. Configure all three names
as required checks after provisioning the GPU runner. Missing
`DRONEGS_GPU_CI=true`, or a fork PR that cannot safely run on the self-hosted
runner, fails a selected GPU gate rather than treating it as qualified.
Do not enable untrusted fork code on the runner to work around this restriction.

The `dronegs-gpu-qualification.yml` workflow runs native CUDA tests only after an
explicit manual dispatch or a GPU-relevant PR/merge-queue change selected by
`select_gpu_validation.py`; it has no scheduled trigger. It uses the same
development container on a self-hosted runner, then verifies driver injection
in each production CUDA runtime image. It requires the dedicated repository
runner labelled `msi`, `gpu` and `cuda` plus the repository variable
`DRONEGS_GPU_CI=true`. The workflow
writes the commit, runner and result to the GitHub job summary and retains the
complete `gpu-validation.log` as a commit-scoped artifact for 30
days, including failed attempts. This artifact is native GPU qualification evidence; a
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
self-hosted GPU runner before manually requesting physical-GPU qualification.

The hosted CI builds the dashboard API and bounded IA runtime, while the frontend
runtime is built and scanned in its own job. The CUDA workflow builds the COLMAP base and
local Gaussian runtime images, then generates a CycloneDX JSON SBOM with Syft
and a HIGH/CRITICAL JSON vulnerability report with Trivy for each image.
Fixable HIGH and CRITICAL findings fail the image job; unfixed findings remain visible
in the report without making a release impossible. The commit-scoped
`supply-chain-<image>-<sha>` artifacts are retained for 30 days, including
failed jobs. Syft and Trivy container tags and multi-architecture digests are
pinned in `.github/workflows/ci.yml` and
`.github/workflows/cuda-containers.yml`. The API and bounded IA images pin the
multi-architecture `python:3.12-slim` index digest, and both frontend stages
pin the `node:20-alpine` index digest, so a rebuild cannot silently select a
different upstream filesystem. Refresh these digests only as explicit,
reviewed dependency updates.

The tag-triggered `promote-images.yml` workflow is the only cryptographic
production promotion path. It accepts a GitHub-verified signed platform tag on
`main` only after successful commit-scoped CI, CUDA container, physical GPU and
CodeQL runs. Hosted builders push five GHCR images, reject fixable
HIGH/CRITICAL findings, publish BuildKit and GitHub provenance, sign every
digest through Sigstore OIDC and emit a keyless-signed manifest containing
image, SBOM and qualification identities. Configure the
`production-promotion` GitHub environment with required reviewers before
creating a release tag. The local publisher remains a manual preproduction
tool and does not provide cryptographic provenance.

The frontend runtime stage also removes npm/npx:
the production Next.js server only needs Node.js, so package-manager tooling
and its unused dependency tree are not shipped in the deployable image.

Frontend source changes retain the regular lint, unit, production-build and
Playwright path. The separate frontend image/SBOM/Trivy job is intentionally
selected only when the Dockerfile, npm manifests/lock or global Docker context
changes; ordinary React or CSS edits do not repeat the container build.

The `.in` files under `requirements/` list direct dependencies. Regenerate the
corresponding lock after intentionally changing one of them:

```bash
python -m piptools compile --generate-hashes --strip-extras requirements/api.in
python -m piptools compile --generate-hashes --strip-extras requirements/processing.in
python -m piptools compile --generate-hashes --strip-extras requirements/colmap.in
python -m piptools compile --generate-hashes --strip-extras --allow-unsafe requirements/dev.in
```

Use the Python version of the corresponding runtime image when regenerating a
service lock. Runtime images, CI, and `scripts/bootstrap-dev.sh` install these
locks with `--require-hashes`; a dependency line without an approved artifact
hash therefore fails closed.

`requirements/ia-extra.txt` is intentionally the exception: it is a
direct-dependency compatibility layer over the digest-pinned Ultralytics base
image, not an independently resolved environment. Do not resolve and pin its
transitive Torch, CUDA, or NVIDIA dependencies independently from that image.

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

Worker cancellation is durable and generation-aware. The API commits the
PostgreSQL cancellation state and its outbox command atomically; all worker
replicas then consult that state even if Kafka delivered the control event to a
different replica. `CANCELLATION_POLL_SECONDS` controls the rate of negative
database checks and defaults to `2`. Keep cross-registry propagation, stale
attempt rejection, and polling-rate tests in `tests/test_cancellation.py` when
changing this contract.

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


## Hardware WebGPU image tests

The GSTile Playwright project requires a real V4 bundle and hardware WebGPU.
It refuses software adapters; do not add SwiftShader or software-fallback flags.
The fixture must be suitable for visible rendering. API routes are mocked;
packs, range requests, decode, GPU rendering and camera interactions are real.

On a host exposing hardware WebGPU to Chromium, build the frontend then run
from `app4-dashboard/frontend`:

```bash
GSTILE_BUNDLE_ROOT=/absolute/path/to/current-v4-bundle   corepack npm run test:e2e -- --project=gstile-webgpu --workers=1
```

On this WSL host, use Windows Chrome and Windows Node. First build and start
Next in WSL on `127.0.0.1:3000`. Then, in a second WSL terminal:

```bash
cd /home/olivier/droneAI/app4-dashboard/frontend
export GSTILE_BUNDLE_ROOT=/absolute/path/to/current-v4-bundle
export GSTILE_EXTERNAL_SERVER=1
export GSTILE_CHROME_EXECUTABLE='C:\Program Files\Google\Chrome\Application\chrome.exe'
export CI=true
export WSLENV="${WSLENV:+$WSLENV:}GSTILE_BUNDLE_ROOT/p:GSTILE_EXTERNAL_SERVER:GSTILE_CHROME_EXECUTABLE:CI"
test_cli=$(wslpath -w "$PWD/node_modules/playwright/cli.js")
"/mnt/c/Program Files/nodejs/node.exe" "$test_cli" test   --project=gstile-webgpu --workers=1 --retries=0 </dev/null
```

Windows Chrome uses a temporary browser profile; no personal profile is used.
Windows Node reads the authoritative source and pure JavaScript Playwright
dependencies over WSL UNC paths. No Windows repository clone is needed.
`GSTILE_EXTERNAL_SERVER=1` means the caller owns the Next process and must stop
it afterwards. Omit it when Playwright should manage its own WSL server.
Adapter identity is attached to the report. The small synthetic cleanup fixture
does not qualify large-scene LOD transitions or scientific image quality.

## Source security and browser policy

`.github/workflows/codeql.yml` scans Python and JavaScript/TypeScript with
the security-extended query suite on PR candidates, merge-queue candidates and
manual runs. Its selector runs only the language affected by the candidate;
unknown source representations and malformed diffs run both. A normal merge
does not repeat the scan after the merge-queue result.
CodeQL results still require review in GitHub; a workflow file is not a passed
scan or a configured merge-blocking alert policy.

The Next.js configuration enforces `base-uri`, `object-src` and
`frame-ancestors` restrictions, disables the powered-by header, and sets
nosniff, referrer, permissions and HSTS headers. A stricter CSP is report-only:
Next inline hydration requires nonce/hash integration before enforcing
`script-src 'self'`. Violations currently appear in browser diagnostics;
no collection endpoint is configured. Qualify S3 upload URLs, tiles, WebSocket
connections and PlayCanvas workers on the target deployment before enforcing
that policy. HSTS intentionally excludes includeSubDomains and preload.


## Reverse-proxy client identity

Helm passes `dashboardApi.proxy.trustedCidrs` to Uvicorn's
`--forwarded-allow-ips`. Development trusts loopback only. Staging and
production reject missing, placeholder, wildcard, internet-wide and
whitespace-bearing values; set the exact direct Traefik/load-balancer peers or
pod-network CIDRs after inspecting the live route. This controls whether
`request.client.host` can use the normalized forwarded client address for the
peer rate-limit bucket. It does not prove the LB strips spoofed forwarding
headers: qualify that behavior with two clients and a forged-header negative
test in the target cluster.


## NetworkPolicy baseline

Protected Helm overlays enable six L3/L4 policies. API, control worker,
frontend and dynamically created Stage Job pods are selected by stable labels,
default-denied in both directions (Stage Jobs have no ingress allowance), then
given DNS and explicit service-port egress. Only the configured ingress
namespace reaches HTTP ports, and only the monitoring namespace reaches
metrics. Stage Jobs receive no Kafka allowance, no service-account token and no
dedicated Kubernetes API allowance.

Portable Kubernetes NetworkPolicy cannot restrict an external HTTPS service by
DNS name, so port 443 remains destination-agnostic for S3/model downloads and
the control worker's Kubernetes API. Because this shared port also permits
HTTPS from Stage Jobs, the policy alone does not prove that the API endpoint is
network-unreachable; the absent service-account token is the authorization
boundary. This is a documented residual boundary.
Qualify the policies with the target CNI, DNS, external S3/model endpoints,
metrics scraper, ingress, Stage Jobs and Job creation before rollout. A CNI
with audited FQDN rules can narrow HTTPS later.
