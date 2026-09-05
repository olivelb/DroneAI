# Deployment with bounded Stage Jobs

DroneAI's supported compute path uses Kubernetes Stage Jobs. The local
Compose deployment and Kafka IA/processing workers have been retired.
`compose.test.yaml` provides isolated test dependencies and an HTTP control
plane; it does not execute missions.

Deploy the frontend and COLMAP application images from the same qualified
release. See the [GSTile production profile](docs/contracts/gstile-production-defaults-v1.md).
Rebuild the base image when its locked Python dependencies change.

## Ubuntu and WSL workstation

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI
./deploy.sh distributed --stage-jobs "$(git rev-parse --short=7 HEAD)"
```

The Git SHA is mandatory. The script builds four application images:
`drone-colmap`, `drone-ia`, `drone-dashboard-api` and
`drone-dashboard-frontend`. The API image also runs migrations and the separate
control worker. There is no processing image.

For a configuration-only redeployment, reuse the exact already-built tag:

```bash
./deploy.sh distributed --stage-jobs <built-git-sha> --no-build
```

This workstation entrypoint uses development credentials. A public staging or
production deployment must use the protected Helm overlays, external Secrets,
RLS roles and promoted OCI executor digests documented in
[the OVHcloud guide](docs/OVHCLOUD_PREPROD.md). Do not expose the workstation
configuration as a public production service.

## Host requirements and setup

Use Ubuntu or WSL2 Ubuntu with a compatible NVIDIA driver, systemd, outbound
package/image access, at least 16 GiB RAM (24 GiB recommended), and enough disk
space for CUDA images and build caches. A clean build may temporarily require
60–100 GiB.

K3s requires systemd. If necessary, enable it in `/etc/wsl.conf`:

```ini
[boot]
systemd=true
```

Restart WSL from Windows after changing that configuration. Deployment checks
both host `nvidia-smi` and a CUDA 12.9.2 container, then installs or reuses
Docker, NVIDIA Container Toolkit, K3s, Helm and the NVIDIA device plugin.
The current plugin configuration exposes physical GPUs without time slicing.
Stage resource classes and scheduler concurrency govern GPU allocation.

The script prepares pinned Ceres/COLMAP sources, builds or verifies images,
imports them into K3s, configures persistent infrastructure and work drives,
runs migrations, and waits for API/frontend readiness. It does not erase
existing mission data.

## Options

| Option | Purpose |
|---|---|
| `--stage-jobs GIT_SHA` | Required immutable local application image tag |
| `--base` | Rebuild the base and all services without cache |
| `--no-build` | Reuse existing images with the supplied tag |
| `--skip-host-setup` | Validate without installing host runtimes |
| `--data-root PATH` | Persistent infrastructure/workspace root |
| `--dashboard-port PORT` | Dashboard NodePort, default 30000 |
| `--api-port PORT` | API NodePort, default 30080 |
| `--minio-console-port PORT` | MinIO console NodePort, default 30090 |
| `--minio-api-port PORT` | Browser-facing S3 NodePort, default 30091 |

Keep the data root stable across upgrades. HostPath volumes are retained.
Work-drive discovery adds only actual writable mounts below `/mnt`, `/media`
and `/data`, with isolated `.droneai/colmap-work` directories.
Set `DRONEAI_DISCOVER_WORK_DRIVES=0` to expose only the primary root.

The workspace configuration remains under `colmapWorker.workVolume`, even
though the worker Deployment is retired. A cloud deployment may use:

```yaml
colmapWorker:
  enabled: false
  workVolume:
    persistentClaim:
      enabled: true
      name: drone-ai-colmap-work
      storageClass: csi-cinder-high-speed
      size: 100Gi
    drives:
      - name: cloud-workspace
        existingClaim: drone-ai-colmap-work
        label: Cloud persistent workspace
    default: cloud-workspace
```

The PVC is created for Stage Jobs when requested. Helm validates the selected
drive even without a COLMAP Deployment. Host directories must exist and be
writable by the image's non-root UID/GID 10001; missing drives are not silently
created on the node root filesystem.

## Executable stages

| Stage | Command |
|---|---|
| Reconstruction | `python3 app1-colmap/stage_executor.py reconstruction` |
| Gaussian training | `python3 app1-colmap/stage_executor.py gaussian_training` |
| Gaussian filtering | `python3 app1-colmap/stage_executor.py gaussian_filtering` |
| RGB/height rasterization | `python3 app1-colmap/stage_executor.py rasterization` |
| Detection and independent map analyses | `python3 app2-ia/stage_executor.py` |
| Gaussian viewer preparation | `python3 app1-colmap/stage_executor.py gaussian_viewer` |

Jobs use read-only root filesystems, non-root identities, bounded work/cache
volumes, deadlines and automatic TTLs. PostgreSQL stores attempts and artifact
lineage. Organization-scoped Manifest v3 workspaces carry verified immutable
state between stages. Retry selects exact parent artifact UUIDs.

Standalone map analyses use their own detection attempts and do not replace
the pipeline detection layer. Their final GeoJSON is versioned; optional
PostGIS features commit atomically with the artifact. Cancelling one analysis
does not cancel the mission. Retry uses the original raster.

The control worker enforces owner, mission, global and resource-class limits.
Protected environments also require distinct stage Secrets and active
PostgreSQL RLS. Keep all image, tenant and resource guards enabled.

## Model access

YOLO OBB needs no Hugging Face token. SAM 3 needs approved model access and an
`HF_TOKEN` supplied through the runtime Secret; it is not baked into images.
Its immutable model revision is configured at `stageJobs.sam3.revision`.
Detection Jobs use bounded ephemeral caches under `/cache` and the verified
image-baked YOLO model under `/opt/modelzoo`.

## Operations and verification

```bash
sudo k3s kubectl get pods -n drone-ai
sudo k3s kubectl get jobs -n drone-ai
sudo k3s kubectl logs -n drone-ai deployment/dashboard-api
sudo k3s kubectl logs -n drone-ai job/<stage-job>
```

Under WSL the printed NodePort URLs use its current address. Redeploy with
`--no-build` and the same image tag if that address changes.

For acceptance: upload a dataset, select the map or facade process and a
qualified profile, launch, inspect stage progress and artifacts, then inspect
raster/vector alignment and export to QGIS. Also exercise independent analysis
creation, cancellation, failure/retry and GeoJSON download.

CPU tests, PostGIS integration and mocked browser journeys do not replace a
new scientific campaign on real datasets. The cleanup's evidence and remaining
scope are recorded in the
[cleanup audit](docs/audits/2026-08-28-current-production-cleanup.md).

## Audit migration 0038 and execution cleanup

Read [the September audit rollout](docs/audit-2026-09-05.md) before upgrading.
It covers the artifact-reference fence, generic cleanup proof, Pod-list RBAC,
destination-specific egress, per-mission volume mounts, Node 24 standalone,
runtime API/CSP origins, database connection budgets and opt-in CAS maintenance.
Never treat an accepted Job DELETE or an empty current-object listing as proof
of complete physical erasure.
