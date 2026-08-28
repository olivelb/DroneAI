# One-command local deployment

For the tiler and renderer, deploy the frontend and COLMAP application images
from the same qualified release. The [GSTile production profile](docs/contracts/gstile-production-defaults-v1.md)
documents effective defaults, explicit rollbacks and the dedicated viewer Job's
OpenBLAS startup policy. Rebuild an outdated base when its locked Python
dependencies differ; copying application code does not update that runtime.

DroneAI has one public build-and-deploy entry point:

```bash
./deploy.sh local
./deploy.sh distributed
```

Both modes build the same five application images and expose the complete
operator dashboard. Every mission uses MinIO/S3, PostgreSQL/PostGIS,
COLMAP/GLOMAP, DroneGS, COG processing and the selected AI backend. Kafka owns
platform events and the compatibility worker path; the qualified distributed
path dispatches the scientific stages as Kubernetes Jobs. The deployment modes
differ as follows:

| Mode | Orchestrator | Intended use |
|---|---|---|
| `local` | Docker Compose | simplest workstation and WSL deployment |
| `distributed` | single-node K3s + Helm | Kubernetes/Helm validation and production-like operations |

The chart contains the qualified resource-aware stage Job control plane. All
five one-shot executors completed the BIGZEN K3s/RTX 3090 Q3 mission documented
in the [Chapelle qualification report](docs/benchmarks/chapelle-banyuls-p4-fast-e2e-2026-08-09.md).
The generic chart default stays `false` to prevent accidental Job dispatch with
mutable or incomplete image settings; `deploy.sh distributed` activates it
only when `STAGE_JOBS_IMAGE_TAG` supplies a traceable Git-SHA tag for the local
K3s workflow. Protected Helm environments reject tags and require promoted OCI
digests. The fused Kafka workers remain available as an explicit compatibility
path.

The infrastructure-free Python runner in `tools/run_local_pipeline.sh` remains
available for scientific diagnostics, but it is not the dashboard deployment.

## Clone to dashboard

On Ubuntu or WSL2 Ubuntu:

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI

# Recommended first workstation deployment
./deploy.sh local
```

For the Kubernetes topology:

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI
export STAGE_JOBS_IMAGE_TAG="$(git rev-parse --short=7 HEAD)"
./deploy.sh distributed
```

The script prints the effective dashboard, API and MinIO URLs only after the
runtime is healthy. It is safe to rerun. Use `--no-build` after a successful
build to redeploy or refresh runtime configuration without rebuilding images:

```bash
./deploy.sh local --no-build
./deploy.sh distributed --no-build
```

## Host requirements

The script supports Ubuntu and Ubuntu under WSL2. It installs missing host
packages when possible. The non-installable prerequisites are:

- a compatible NVIDIA GPU and host driver;
- outbound network access for packages, source archives and container images;
- at least 16 GiB RAM; 24 GiB or more is recommended;
- sufficient disk space for CUDA/COLMAP images and build cache. A clean build
  can require 60–100 GiB temporarily.

`local` works with native Docker Engine or Docker Desktop WSL integration.
`distributed` requires systemd because K3s runs as a system service. For WSL,
enable it in `/etc/wsl.conf` when necessary:

```ini
[boot]
systemd=true
```

Then run `wsl --shutdown` once from Windows and reopen Ubuntu.

The script validates both host `nvidia-smi` and a CUDA 12.9.2 Docker container
before building the large images. In distributed mode it also validates the
K3s NVIDIA RuntimeClass, installs the NVIDIA device plugin, enables two
time-sliced allocation slots and checks the allocatable GPU resource.

## What the script performs

The shared part of both modes:

1. validates Ubuntu/WSL, RAM, disk and the NVIDIA GPU;
2. installs or reuses Docker, Docker Compose and NVIDIA Container Toolkit;
3. clones pinned Ceres and COLMAP sources and checksum-verifies generated
   dependency caches;
4. builds `drone-colmap-base` when absent and builds the five service images;
5. starts the runtime, applies Alembic migrations and waits for health;
6. verifies the dashboard and API over their browser-facing URLs.

`local` additionally:

- creates a Docker Compose project named `droneai-local`;
- starts Kafka, creates all pipeline topics, starts MinIO and its bucket,
  starts PostGIS, runs migrations and starts all workers;
- uses named Docker volumes for service data and a bind-mounted persistent
  COLMAP workspace below the selected data root.

`distributed` additionally:

- installs or reuses K3s and Helm;
- installs the NVIDIA device plugin with the `nvidia` RuntimeClass;
- imports local service images into K3s containerd, skipping matching digests;
- creates the optional `hf-token` secret, renders portable host paths, sizes
  memory limits from available RAM and deploys `charts/drone-ai`;
- discovers writable storage mounted below `/mnt`, `/media` or `/data` and
  exposes only those real mount points as additional work drives;
- selects free NodePorts when another service owns a requested default;
- injects a WSL-reachable API and MinIO origin into the dashboard.

## Options

```text
--base                     no-cache rebuild of the base and all services
--no-build                 reuse the five existing service images
--skip-host-setup          validate but do not install host runtimes
--data-root PATH           local/distributed persistent-data root
--dashboard-port PORT      dashboard port, default 30000
--api-port PORT            API port, default 30080
--minio-console-port PORT  MinIO console port, default 30090
--minio-api-port PORT      browser-facing MinIO port, default 30091
```

Examples:

```bash
./deploy.sh local --base

./deploy.sh distributed \
  --data-root "$HOME/droneai-data" \
  --dashboard-port 30100 \
  --api-port 30180
```

The distributed data root must remain stable across upgrades because
Kubernetes hostPath persistent volumes are intentionally retained.

## Qualified bounded stage Jobs

Set `STAGE_JOBS_IMAGE_TAG` to the exact commit being built to deploy the
five-stage DAG:

```bash
export STAGE_JOBS_IMAGE_TAG="$(git rev-parse --short=7 HEAD)"
./deploy.sh distributed

# Reuse those immutable images on a configuration-only redeploy.
./deploy.sh distributed --no-build
```

This mode disables the long-running COLMAP and IA Deployments, scales the
compatibility processing worker to zero and configures these commands:

| Stage | Image | One-shot command |
|---|---|---|
| Reconstruction | `drone-colmap:<git-sha>` | `python3 app1-colmap/stage_executor.py reconstruction` |
| Gaussian training | `drone-colmap:<git-sha>` | `python3 app1-colmap/stage_executor.py gaussian_training` |
| Gaussian filtering | `drone-colmap:<git-sha>` | `python3 app1-colmap/stage_executor.py gaussian_filtering` |
| Ortho/DEM rasterization | `drone-colmap:<git-sha>` | `python3 app1-colmap/stage_executor.py rasterization` |
| Detection | `drone-ia:<git-sha>` | `python3 app2-ia/stage_executor.py` |

Each Job uses the `nvidia` RuntimeClass for its GPU resource class, a read-only
root filesystem, bounded scratch/cache volumes, no Kubernetes retry and an
automatic TTL. PostgreSQL stores the attempts and exact artifact edges; S3 is
the durable hand-off. A retry selects exact parent artifact UUIDs and never
overwrites or relabels an earlier attempt.

Leaving `STAGE_JOBS_IMAGE_TAG` unset intentionally deploys the compatibility
Kafka workers. Do not mix the two execution modes for one new mission.

## Work-drive discovery

Both one-command modes always expose the persistent workspace below
`--data-root`. They also inspect the operating system mount table and add
writable filesystems mounted below `/mnt`, `/media` or `/data`. On WSL this
means that `C:`, `D:` or another Windows drive is shown only while its exact
`/mnt/<letter>` mount is present and writable. Each discovered filesystem gets
an isolated `.droneai/colmap-work` directory; the dashboard never receives the
filesystem root.

Set `DRONEAI_DISCOVER_WORK_DRIVES=0` to expose only the primary data root.
Manual/cloud Helm installations can mount a pre-existing persistent claim:

```yaml
colmapWorker:
  workVolume:
    drives:
      - name: cloud-workspace
        existingClaim: drone-ai-colmap-work
        label: "Cloud persistent workspace"
    default: cloud-workspace
```

Helm rejects a default that is not backed by an `emptyDir`, verified host
directory or PVC configuration. All application host paths use Kubernetes
`Directory`, not `DirectoryOrCreate`, so a missing disk cannot silently become
a Kafka, MinIO, PostgreSQL, model-cache or COLMAP directory on the node's root
filesystem.

## Hugging Face and AI backends

YOLO OBB does not require a Hugging Face token. The deployment creates an
empty compatible value when `HF_TOKEN` is not set.

SAM 3 requires approved access to its gated distribution:

```bash
export HF_TOKEN=hf_...
./deploy.sh local
# or
./deploy.sh distributed
```

The token is passed at runtime and is never compiled into an image or printed
by the deployment script.

## Resource safety and container identities

Every DroneAI application service, including COLMAP, runs with a fixed
non-root identity in Kubernetes. Root filesystems are read-only and the chart
mounts only the required temporary, cache and workspace directories as
writable volumes. Local Compose applies the same non-root, read-only-root,
capability-drop and no-new-privileges policy to COLMAP and provides `/tmp` as a
bounded tmpfs. COLMAP uses UID/GID `10001`; deployment tooling creates the
primary work directory with mode `0770` and that ownership. Administrators
adding an existing `hostPath` or external disk must make its dedicated
`.droneai/colmap-work` directory writable by UID/GID `10001` before advertising
it.

AI campaign finalization rejects pathological aggregate payloads instead of
allowing an unbounded in-memory allocation. The producer and consumer share
`tileResults.maximumBytes`; other Helm defaults are configurable at
`processingWorker.analysis`. The corresponding environment variables are:

```text
ANALYSIS_MAX_TILE_RESULT_BYTES=10485760
ANALYSIS_MAX_AGGREGATE_RESULT_BYTES=268435456
ANALYSIS_MAX_RAW_DETECTIONS=100000
ANALYSIS_MAX_FINAL_DETECTIONS=50000
ANALYSIS_MAX_TILE_ATTEMPTS=5
```

Raster PNG responses retain their one-hour private browser cache and are
protected by a PostgreSQL token bucket shared by API replicas in staging and
production. The bucket key is the authenticated subject, not a proxy-dependent
source address. Configure it through
`dashboardApi.tiles` or `DRONEAI_TILE_RATE_LIMIT_PER_MINUTE`,
`DRONEAI_TILE_RATE_LIMIT_BURST` and
`DRONEAI_TILE_RATE_LIMIT_MAX_CLIENTS`. Local development retains its bounded
in-process implementation.

## Operating the local mode

Compose binds the dashboard, API and MinIO ports to `127.0.0.1` by default.
This is intentional because local mode uses development credentials. To allow
access from the LAN, opt in explicitly and configure matching public hosts,
CORS and authentication:

```bash
export DRONEAI_BIND_ADDRESS=0.0.0.0
export DRONEAI_ACCESS_HOST=<workstation-lan-address>
./deploy.sh local
```

```bash
# Inspect containers and health
docker compose -p droneai-local -f compose.local.yaml ps

# Follow service logs
docker compose -p droneai-local -f compose.local.yaml logs -f

# Stop containers while preserving named volumes
docker compose -p droneai-local -f compose.local.yaml down

# Remove containers and local persistent volumes
docker compose -p droneai-local -f compose.local.yaml down --volumes
```

The last command permanently removes local databases, datasets and results.

## Operating the distributed mode

```bash
sudo k3s kubectl get pods -n drone-ai
sudo k3s kubectl logs -n drone-ai deployment/dashboard-api
sudo k3s kubectl get jobs -n drone-ai
sudo k3s kubectl logs -n drone-ai job/<stage-job>

# Redeploy after code or WSL address changes, without rebuilding
./deploy.sh distributed --no-build
```

Under WSL, the K3s NodePort link uses the current WSL address because Windows
localhost forwarding does not consistently proxy K3s NodePorts. Rerun the
script with `--no-build` after a full WSL restart if that address changes.

## Dashboard end-to-end test

The manual acceptance journey below remains useful with real services and
datasets. Development and CI additionally run the automated Playwright browser
journeys documented in [`app4-dashboard/frontend/README.md`](app4-dashboard/frontend/README.md).

1. Open the dashboard URL printed by `deploy.sh`.
2. Enter a dataset name and select all images for one flight or survey.
3. Upload and select the resulting dataset folder.
4. In Reconstruction, choose **Cartographie aérienne** or **Façade HD** under
   **Processus de production**, then select a work drive.
5. For a map, review the alignment preset, retriangulation and projected CRS.
   For a facade, review the exclusion ranges and local scale; the qualified
   Caspar/DroneGS values are loaded from the API profile.
6. Select a DroneGS profile and AI backend. For SAM 3, enter the segmentation
   prompt/classes; choose a YOLO OBB variant only for a YOLO mission.
7. Launch the mission. In bounded Job mode, a map advances through the five
   durable stages shown by the live mission monitor. A facade ends after its
   local RGB/depth products and reports; no aerial detection Job is released.
8. Open Results to inspect the available raster products. Map missions also
   expose vector detections, measurements, search and manual annotations;
   facade rasters remain explicitly labelled as local and CRS-free.
9. Open Export, download the COG and a GeoPackage using the raster CRS, then
   verify both layers superpose in QGIS. Repeat with WGS84 or a custom
   `EPSG:<code>` when that delivery contract is required.

Mission state, object-store outputs and database records survive process
restarts. The dashboard exposes resume, retry and cancellation controls for
the supported pipeline stages.
