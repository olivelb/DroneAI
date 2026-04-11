# DroneAI Pipeline

This repository contains a complete local photogrammetry and detection pipeline built from five microservices:

1. `app1-colmap`: COLMAP reconstruction and orthomosaic generation
2. `app2-ia`: dual-backend tile detection with Ultralytics YOLO OBB or Meta SAM 3, with polygon output preserved for downstream rendering
3. `app3-processing`: tile slicing, overlap-deduplicated detection aggregation, annotated orthomosaic generation
4. `app4-dashboard/api`: FastAPI control and status API, including detector backend and SAM 3 prompt parameters in mission payloads
5. `app4-dashboard/frontend`: Next.js dashboard UI with backend selection and SAM 3 prompt entry

The stack runs locally on Kubernetes via K3s. Kafka is deployed inside the cluster from `kafka-local.yaml`; you do not install Kafka separately on the host.

For runtime architecture, Kafka contracts, orthomosaic construction details, and processing-worker behavior, see `DOCUMENTATION.md`.

## Showcase

![Vehicle detection on orthomosaic](docs/showcase_vehicle_detection.png)

Georeferenced vehicle detection on a drone orthomosaic. The pipeline reconstructs the scene with COLMAP and 3D Gaussian Splatting, slices the resulting orthophoto into tiles, runs SAM 3 prompt-based segmentation on each tile, deduplicates overlapping detections, and reprojects the results back onto the full orthomosaic with real-world GPS coordinates (lat/lon labels).

## What is required in the repo

For a working installation, these files are part of the install path and must be present:

- `app1-colmap/Dockerfile.base`
- `app1-colmap/Dockerfile`
- `app2-ia/Dockerfile`
- `app3-processing/Dockerfile`
- `app4-dashboard/api/Dockerfile`
- `app4-dashboard/frontend/Dockerfile`
- `shared/config.py`
- `shared/pipeline_params.py`
- `kafka-local.yaml`
- `dashboard-api-rbac.yaml`
- `build_and_deploy.sh`
- `setup.sh`
- `deploy_app1_colmap.sh`
- `deploy_app2_ia.sh`
- `deploy_app3_processing.sh`
- `deploy_app4_api.sh`
- `deploy_app4_frontend.sh`

The rest of the repository is the application source code used by those images.

## Pre-requisite source setup

The Docker build `COPY`s several external source trees into the image. These are **not** bundled in this repository and must be prepared before the first build. Without them, `docker build` will fail immediately.

### 1. Ceres Solver 2.2.0

```bash
git clone --branch 2.2.0 --depth 1 https://github.com/ceres-solver/ceres-solver.git app1-colmap/ceres-solver
```

### 2. COLMAP 4.0.1

```bash
git clone --branch 4.0.1 --depth 1 https://github.com/colmap/colmap.git app1-colmap/colmap-local
```

COLMAP's build uses CMake `FetchContent` to download PoseLib and faiss at configure time. Because Docker BuildKit network access can be unreliable (especially under WSL2), the Dockerfile expects pre-downloaded archives and patches the URLs to `file://` paths. Apply the patch:

```bash
mkdir -p app1-colmap/colmap-deps

# Download the exact archives that COLMAP 4.0.1 expects (SHA256-verified):
wget -O app1-colmap/colmap-deps/poselib.zip \
  https://github.com/PoseLib/PoseLib/archive/f119951fca625133112acde48daffa5f20eba451.zip

wget -O app1-colmap/colmap-deps/faiss.zip \
  https://github.com/ahojnnes/faiss/archive/36b77353dc435383e0c23a709e7997a29d049041.zip

# Patch FetchContent URLs to use local file:// paths inside the Docker build:
sed -i 's|https://github.com/PoseLib/PoseLib/archive/f119951fca625133112acde48daffa5f20eba451.zip|file:///tmp/colmap-deps/poselib.zip|' \
  app1-colmap/colmap-local/src/thirdparty/CMakeLists.txt

sed -i 's|https://github.com/ahojnnes/faiss/archive/36b77353dc435383e0c23a709e7997a29d049041.zip|file:///tmp/colmap-deps/faiss.zip|' \
  app1-colmap/colmap-local/src/thirdparty/CMakeLists.txt
```

### 3. LichtFeld-Studio

```bash
git clone https://github.com/MrNeRF/LichtFeld-Studio.git LichtFeld-Studio
cd LichtFeld-Studio && git submodule update --init --recursive && cd ..
```

### 4. vcpkg (for the LichtFeld build stage)

LichtFeld-Studio pins vcpkg at a specific baseline commit (`c3867e714dd3a51c272826eea77267876517ed99`). The Docker build expects a pre-cloned vcpkg tree at `.docker-vcpkg/`:

```bash
git clone https://github.com/microsoft/vcpkg.git .docker-vcpkg
git -C .docker-vcpkg checkout c3867e714dd3a51c272826eea77267876517ed99
```

### Quick-reference summary

| Directory | Source | Version / Commit |
| --- | --- | --- |
| `app1-colmap/ceres-solver/` | `https://github.com/ceres-solver/ceres-solver.git` | tag `2.2.0` |
| `app1-colmap/colmap-local/` | `https://github.com/colmap/colmap.git` | tag `4.0.1` (then patch FetchContent URLs) |
| `app1-colmap/colmap-deps/` | PoseLib + faiss zip archives | see wget commands above |
| `LichtFeld-Studio/` | `https://github.com/MrNeRF/LichtFeld-Studio.git` | latest (or pin to a known-good commit) |
| `.docker-vcpkg/` | `https://github.com/microsoft/vcpkg.git` | commit `c3867e714dd3a51c272826eea77267876517ed99` |

## Docker build architecture

The base image (`app1-colmap/Dockerfile.base`) is a four-stage multi-stage build. Docker BuildKit runs the first three stages in parallel:

```text
Stage 1: builder           (nvidia/cuda:12.8.1-devel-ubuntu22.04)
  → Ceres Solver + COLMAP from source
  → Stages selective CUDA runtime libs into /cuda-slim/

Stage 2: pip-builder       (nvidia/cuda:12.8.1-devel-ubuntu22.04)
  → PyTorch (cu124) + gsplat 1.5.3 + Python packages

Stage 3: lichtfeld-builder (nvidia/cuda:12.8.1-devel-ubuntu24.04)
  → LichtFeld-Studio via gcc-14 + CMake 4.x + vcpkg
  → Ubuntu 24.04 is required because LichtFeld needs gcc-14

Stage 4: runtime           (nvidia/cuda:12.8.1-base-ubuntu22.04)
  → Copies outputs from all three builder stages
  → Copies nvimgcodec extension plugins (nvjpeg_ext) for GPU JPEG decoding
  → Only the CUDA libs actually needed at runtime (~1.5 GB vs 2.6 GB full suite)
```

The final `Dockerfile` adds the Python application code on top of the base image.

### CUDA compute architecture targeting

The build currently targets **Ampere** (RTX 3090, A100) and **Ada Lovelace** (RTX 4090) GPUs:

- COLMAP / Ceres: `-DCMAKE_CUDA_ARCHITECTURES="86-real;89-real"`
- PyTorch / gsplat: `TORCH_CUDA_ARCH_LIST="8.6;8.9"`
- LichtFeld-Studio: `BUILD_CUDA_PTX_ONLY=ON` (JIT-compiles for any GPU at runtime)

To support different GPUs, edit `Dockerfile.base` and change the architecture values. Common targets:

| GPU family | CUDA arch code |
| --- | --- |
| Turing (RTX 2080) | `75` |
| Ampere (RTX 3090, A100) | `86` |
| Ada Lovelace (RTX 4090) | `89` |
| Hopper (H100) | `90` |

### Selective CUDA library staging

The runtime image uses `nvidia/cuda:12.8.1-base` (402 MB) instead of the full `runtime` variant (5.5 GB). Only the CUDA libraries actually linked by COLMAP, Ceres, and LichtFeld are copied from the builder stage:

| Library | Size | Needed by |
| --- | --- | --- |
| cuBLAS + cuBLASLt | ~830 MB | COLMAP (feature matching), Ceres |
| cuSPARSE | ~370 MB | Ceres (sparse solvers) |
| cuSOLVER | ~230 MB | Ceres (dense solvers) |
| cuDSS | ~100 MB | Ceres (direct sparse solver) |
| cuRAND | ~130 MB | LichtFeld (random sampling) |
| cudart | ~1 MB | All (included in cuda:base) |

Excluded (not linked by any component): cuDNN, cuFFT, NPP, nvRTC, nvJitLink (~1.1 GB saved).

### Build time and disk space

- **First build**: 30–90 minutes depending on CPU and network speed
- **Transient disk usage**: the three devel-image stages can consume 20+ GB each during the build. Plan for **40–60 GB of free disk** beyond what K3s and your data already use.
- After the build, run `sudo docker builder prune -af && sudo docker image prune -af` to reclaim build cache.

## Host requirements

Use a Linux machine or WSL2 Ubuntu with:

- `sudo` access
- outbound Internet access for apt, Docker image pulls, pip and npm
- NVIDIA GPU support on the host if you want the GPU-backed COLMAP and IA workers
- NVIDIA drivers already installed on the host and `nvidia-smi` working
- enough disk space for Docker images and the COLMAP base image build
- your mission data available under `/mnt/j/workspace`

Recommended minimum host resources:

- 16 CPU threads
- 32 GB RAM minimum, 64 GB preferred
- 1 NVIDIA GPU
- 80+ GB free disk space

## Directory layout expected by the pipeline

The pipeline is configured to use:

- workspace root: `/mnt/j/workspace`
- host path inside containers: `/host/mnt/j/workspace`

Typical mission layout:

```text
/mnt/j/workspace/
  mission_001/
    image1.jpg
    image2.jpg
    ...
```

The Kubernetes manifest mounts the full host root at `/host` inside the worker containers, so the host path must really exist on the machine running K3s.

## Installation overview

Installation has four parts:

1. Install Docker on the host
2. Install the NVIDIA container toolkit on the host
3. Install K3s on the host
4. Build the images and apply the Kubernetes manifests

You can do this in two ways:

1. Automated install with `setup.sh`
2. Manual install step by step

## Option 1: automated installation

From the repository root:

```bash
chmod +x setup.sh build_and_deploy.sh deploy_app*.sh
bash setup.sh
```

What `setup.sh` does:

- installs base packages with `apt`
- installs Docker if it is missing
- installs the NVIDIA container toolkit if it is missing
- installs K3s if it is missing
- writes kubeconfig to `~/.kube/config`
- runs `build_and_deploy.sh`

After the script finishes, verify the deployment:

```bash
kubectl get pods -n kafka
kubectl get svc -n kafka
```

Open the dashboard at:

- frontend: `http://localhost:30000`
- API root: `http://localhost:30080`

## Option 2: manual installation

### 1. Install base packages

```bash
sudo apt-get update
sudo apt-get install -y curl wget git unzip jq apt-transport-https ca-certificates software-properties-common
```

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo usermod -aG docker "$USER"
newgrp docker
```

Validate Docker:

```bash
docker --version
sudo docker info
```

### 3. Install the NVIDIA container toolkit

This is required so Docker can run CUDA-enabled images.

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Validate GPU access in Docker:

```bash
sudo docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi
```

### 4. Install K3s

K3s provides Kubernetes and its own containerd runtime.

```bash
curl -sfL https://get.k3s.io | sh -
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER":"$USER" ~/.kube/config
export KUBECONFIG=~/.kube/config
```

Optional persistence:

```bash
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
```

Validate Kubernetes:

```bash
kubectl get nodes
kubectl get pods -A
```

### 5. Build and deploy the pipeline

From the repository root:

```bash
chmod +x build_and_deploy.sh deploy_app*.sh
bash build_and_deploy.sh
```

For a full rebuild from scratch with Docker cache disabled for the base image and every service image:

```bash
bash build_and_deploy.sh --base
```

What `build_and_deploy.sh` does:

- checks that the Kubernetes secret `hf-token` exists in namespace `kafka` before deploying the IA worker
- builds `drone-colmap-base:latest` if it does not already exist
- imports the base image into K3s
- builds all five application images
- imports all images into K3s containerd
- applies `kafka-local.yaml`
- applies `dashboard-api-rbac.yaml`
- restarts the deployments in namespace `kafka`

Script options:

- `--base`: force a full no-cache rebuild of `drone-colmap-base:latest` and all application images before importing them into K3s and restarting the stack

Before the first deployment of the SAM 3-enabled IA worker, create the Hugging Face token secret outside git:

```bash
export HF_TOKEN=your_huggingface_token
sudo kubectl -n kafka create secret generic hf-token --from-literal=HF_TOKEN="$HF_TOKEN"
```

The `ia-worker` deployment reads `HF_TOKEN` from that secret and mounts a persistent host cache at `/var/lib/drone-ai/huggingface-cache`, exposed inside the container as `/cache/huggingface`, so the SAM 3 model files stay cached across pod restarts.

### 6. Verify the deployment

```bash
kubectl get all -n kafka
kubectl rollout status deployment/colmap-worker -n kafka
kubectl rollout status deployment/ia-worker -n kafka
kubectl rollout status deployment/processing-worker -n kafka
kubectl rollout status deployment/dashboard-api -n kafka
kubectl rollout status deployment/dashboard-frontend -n kafka
```

## Kafka installation details

Kafka is installed inside Kubernetes by `kafka-local.yaml`.

Important points:

- image used: `apache/kafka:3.7.0`
- namespace: `kafka`
- service name inside cluster: `my-kafka.kafka.svc.cluster.local:9092`
- the applications are already configured to use this broker via environment variables in the manifest

You do not need to install Kafka with apt, Docker Compose, or a separate host service.

To inspect Kafka resources:

```bash
kubectl get pods -n kafka
kubectl logs deployment/kafka-broker -n kafka
```

## Dashboard access

The manifest exposes two NodePort services:

- frontend: `30000`
- API: `30080`

Open:

- `http://localhost:30000`

The frontend talks to the API on port `30080` and receives pipeline status through the WebSocket endpoint `/ws/status`.

The dashboard also uses additional REST endpoints exposed by the API for:

- aggregated mission state: `GET /status/summary`
- Kubernetes pod status: `GET /pods`
- host memory snapshot: `GET /system/resources`
- shared pipeline defaults and parameter metadata: `GET /mission/parameters`
- fusion and image-size estimation for a selected dataset: `POST /mission/estimate`

## Running a mission

1. Put your images in a mission directory under `/mnt/j/workspace`
2. Open `http://localhost:30000`
3. Browse to the input directory
4. Keep the workspace path set to `/mnt/j/workspace`
5. Choose the AI backend:
  - `YOLO OBB` for the Ultralytics oriented-box detector
  - `SAM 3` for prompt-based segmentation
6. If you choose `SAM 3`, enter a short prompt such as `car` or `vehicle`
7. Start the mission from the dashboard

The pipeline flow is:

1. `app1-colmap` consumes `vols-bruts` and produces `images-ortho`
2. `app3-processing` consumes `images-ortho`, slices tiles, and produces `image-tiles`
3. `app2-ia` consumes `image-tiles`, runs the selected detector backend, and produces `tile-detections`
4. `app3-processing` consumes `tile-detections` and writes the final annotated orthomosaic
5. `app4-dashboard/api` streams status to the frontend

### Current app1 orthomosaic path

- `use_mesh_ortho: true` (now labelled **"Use Gaussian Splatting Ortho"** in the UI) selects the 3D Gaussian Splatting orthophoto pipeline.
- The GS pipeline trains a 3DGS model directly from COLMAP undistorted images and the sparse reconstruction, **skipping PatchMatch stereo and fusion entirely** — a significant time saving.
- Training uses **LichtFeld-Studio** (headless CLI mode) with the **MRNF** (Multi-Resolution Neural Field) densification strategy, which grows Gaussians from the initial sparse points to a configurable cap (default 5M). LichtFeld handles image loading, GPU-accelerated NVCODEC decoding, and CUDA rasterisation natively in C++/CUDA with no Python training loop overhead.
- **gsplat** is retained as a rendering-only dependency: orthographic rasterisation of the final model into the GeoTIFF orthomosaic and height map.
- **Fully Anisotropic Gaussian Kernels (FAGK)** with SH-based view-dependent opacity are enabled by default (Tortho-Gaussian).
- After training, a configurable multi-stage post-processing filter chain cleans the model. Each filter can be individually enabled or disabled in the dashboard UI:
  - **Max scale filter** (`gs_filter_max_scale`): removes oversized Gaussians (default: 1.0 world units per axis)
  - **Distance filter** (`gs_filter_dist`): spatial crop as a multiple of the maximum camera distance (default: 1.0)
  - **Opacity filter** (`gs_filter_opacity`): removes nearly transparent Gaussians (default threshold: 0.005)
  - **Needle removal** (`gs_filter_needle`): anisotropy ratio threshold (default 0 = disabled)
  - **Statistical Outlier Removal** (`gs_filter_sor`): k-NN distance outlier removal (default: off)
  - **Connected-Component filter** (`gs_filter_cc`): keeps only the largest connected cluster (default: off)
  - **Z-Floater Removal** (`gs_filter_z_floater`): IQR-based fence on the vertical axis (default: off)
- Filter defaults are deliberately relaxed: LichtFeld's MRNF strategy produces clean models that need minimal post-processing.
- **Nadir fine-tune** phase: after filtering, the model is fine-tuned using only near-nadir training cameras to adapt SH colour coefficients, Gaussian scales, and opacities for the orthographic view direction. Configurable via:
  - **GS Nadir Fine-Tune Iterations** (`gs_nadir_finetune_iters`, default 3000, set to 0 to skip)
  - **GS Nadir Fine-Tune Mode** (`gs_nadir_finetune_mode`): `full` (SH + scales + opacity), `sh_only`, or `off`
  - **GS Nadir Fine-Tune Max Angle** (`gs_nadir_finetune_angle`, default 15°): camera selection threshold
- The cleaned and fine-tuned model is rendered from a virtual orthographic camera into an RGB orthomosaic and a companion height map (DSM) GeoTIFF.
- **PCA path (no Sim3 transform)**: the model stays in the original COLMAP coordinate frame and a rotation matrix `R_geo` is passed to the renderer to orient the virtual nadir camera. This avoids rotating positions and quaternions without rotating SH coefficients, which previously caused colour artefacts.
- **Sim3 path (with alignment transform)**: rotation + scale are applied to the model; translation is kept as float64 for the GeoTIFF origin.
- The height map is shifted to match mean drone EXIF GPS altitude when available, giving real-world elevation values in the output CRS.
- All model coordinates stay in COLMAP-local float32 space during training. The Sim3 geo-alignment is split: rotation+scale applied to the model, translation kept as float64 and folded into the GeoTIFF origin. This avoids the catastrophic float32 precision loss that occurs with UTM-scale translations (~10⁶ m).
- Training progress is reported to the dashboard by parsing LichtFeld's `indicators`-library progress bar output from stdout (`\r`-delimited in-place updates). The LichtFeld MCP HTTP server is only available in GUI mode, not in the headless CLI mode used by the pipeline.
- The following GS parameters are exposed in the dashboard UI (group **Orthomosaic**):
  - **GS Training Iterations** (`gs_iterations`): number of LichtFeld MRNF training iterations (default 30000)
  - **GS Training Image Scale** (`gs_data_factor`): image downscaling factor for training (`auto`, 1, 2, 4, 8)
  - **GS Max Gaussians** (`gs_cap_max`): MRNF maximum Gaussian count (default 5M)
  - **GS Spherical Harmonics Degree** (`gs_sh_degree`): SH degree for view-dependent colour (1, 2, or 3)
  - **GS Spatial Filter** (`gs_filter_enabled`): enable/disable post-training filters
  - **GS Max Scale** (`gs_filter_max_scale`): max activated scale per axis (0 to disable)
  - **GS Distance Multiplier** (`gs_filter_dist`): spatial boundary distance (0 to disable)
  - **GS Opacity Threshold** (`gs_filter_opacity`): minimum opacity to keep (0 to disable)
  - **GS Needle Threshold** (`gs_filter_needle`): max/min scale ratio (0 to disable)
  - **GS Statistical Outlier Removal** (`gs_filter_sor`): enable/disable SOR
  - **GS Connected-Component Filter** (`gs_filter_cc`): enable/disable CC filter
  - **GS Z-Floater Removal** (`gs_filter_z_floater`): enable/disable vertical outlier removal
  - **GS SOR Sigma Multiplier** (`gs_filter_sor_sigma`): sigma multiplier for SOR threshold
  - **GS Nadir Fine-Tune Iterations** (`gs_nadir_finetune_iters`): nadir fine-tune iterations (0 to skip)
  - **GS Nadir Fine-Tune Mode** (`gs_nadir_finetune_mode`): `full`, `sh_only`, or `off`
  - **GS Nadir Fine-Tune Max Angle** (`gs_nadir_finetune_angle`): max camera angle from nadir
  - **Ortho Resolution** (`ortho_mesh_resolution`): output GSD in metres/pixel
- `use_mesh_ortho: false` keeps the legacy point-cloud projection fallback based on `fused.ply` or `fused_geo.ply`, which still requires PatchMatch + fusion.

For SAM 3 missions, access to the gated Hugging Face model must already be approved and the `hf-token` Kubernetes secret must exist before deployment.

App 3 now writes tiles to a mission-scoped directory by default:

- `<mission_dir>/tiles/<vol_id>/tile_*.jpg`

This avoids collisions when multiple missions run against orthomosaics in the same workspace.

App 3 also deduplicates overlap detections before writing the tagged orthomosaic. When the same object is detected on adjacent overlapping tiles, the untiler keeps a single detection and biases toward the largest polygon. The current merge logic first checks whether a smaller polygon centroid or vertices fall inside a larger kept polygon, then falls back to center-distance and bbox-IoU checks.

The current processing deployment defaults are intentionally aggressive to reduce duplicate tags on parked cars:

- `UNTILER_DEDUPE_CENTER_THRESHOLD=40`
- `UNTILER_DEDUPE_IOU_THRESHOLD=0.05`

## Incremental redeploy commands

Use these when only one service changes.

These scripts rebuild a single service image, import it into K3s, reapply the relevant Kubernetes manifest, restart the matching deployment, and wait for rollout completion. They are safe for incremental config changes that live in `kafka-local.yaml`, and `deploy_app4_api.sh` also reapplies `dashboard-api-rbac.yaml`.

They still do not replace the full first-time install path, because `build_and_deploy.sh` remains the only script that rebuilds and stages the entire stack in one pass.

All per-service deploy scripts support:

- `--base`: rebuild that service from scratch with Docker cache disabled
- `--no-restart`: build and import the image into K3s without restarting the deployment

Rebuild COLMAP including the base image:

```bash
bash deploy_app1_colmap.sh --base
```

Rebuild only the COLMAP app layer:

```bash
bash deploy_app1_colmap.sh
```

Rebuild the IA worker:

```bash
bash deploy_app2_ia.sh
```

Rebuild the IA worker from scratch without Docker cache:

```bash
bash deploy_app2_ia.sh --base
```

Rebuild the processing worker:

```bash
bash deploy_app3_processing.sh
```

Rebuild the processing worker from scratch without Docker cache:

```bash
bash deploy_app3_processing.sh --base
```

Rebuild the dashboard API:

```bash
bash deploy_app4_api.sh
```

Rebuild the dashboard API from scratch without Docker cache:

```bash
bash deploy_app4_api.sh --base
```

Rebuild the dashboard frontend:

```bash
bash deploy_app4_frontend.sh
```

Rebuild the dashboard frontend from scratch without Docker cache:

```bash
bash deploy_app4_frontend.sh --base
```

Stage a rebuilt image in K3s without restarting the deployment yet:

```bash
bash deploy_app3_processing.sh --base --no-restart
```

## Local utility scripts

The repository now also contains local maintenance and training helpers that are meant for operator or developer use on the host machine.

### Runtime cleanup

Use `cleanup_runtime.sh` to prune unused Docker and K3s image artifacts, delete completed or failed pods, and remove leftover image tar files created by the deploy scripts.

Examples:

```bash
bash cleanup_runtime.sh --dry-run
bash cleanup_runtime.sh --yes
bash cleanup_runtime.sh --namespace kafka
```

### EAGLE OBB dataset preparation and training

`train_eagle_yolo11_obb.py` converts the Kaggle EAGLE `class xc yc w h angle` labels into the polygon OBB label format accepted by the installed Ultralytics training path, writes a local `data.yaml`, and can optionally launch training.

Prepare only:

```bash
source .train-venv/bin/activate
python train_eagle_yolo11_obb.py --prepare-only
```

Prepare and train:

```bash
source .train-venv/bin/activate
python train_eagle_yolo11_obb.py --epochs 100 --imgsz 416 --batch 8 --device 0 --exist-ok
```

Resume an interrupted run from its last checkpoint:

```bash
source .train-venv/bin/activate
python train_eagle_yolo11_obb.py --resume runs/obb/runs/eagle_obb/yolo11n_obb_eagle_tiled_1024_clean_b1/weights/last.pt
```

If the run directory matches `--project` and `--name`, you can omit the path and the script will look for `PROJECT/NAME/weights/last.pt`.

Resume the newest interrupted run under a project tree automatically:

```bash
source .train-venv/bin/activate
python train_eagle_yolo11_obb.py --auto-resume-latest --project runs/obb/runs/eagle_obb
```

`tile_eagle_obb_dataset.py` creates overlapping tile crops for the EAGLE dataset, keeps only fully contained oriented boxes, removes exact duplicate source labels, and writes a tiled dataset with an Ultralytics-compatible `data.yaml`.

Example:

```bash
source .train-venv/bin/activate
python tile_eagle_obb_dataset.py --source datasets/eagle_yolo11_obb/EagleDatasetYOLO --output datasets/eagle_yolo11_obb_tiled --tile-size 1024 --overlap 256 --background-limit 2
```

Generated datasets under `datasets/eagle_yolo11_obb*` and training outputs under `runs/` are local artifacts and are intentionally not part of the source commit history.

## Troubleshooting

### `drone-colmap-base:latest` not found

The COLMAP worker depends on the base image from `app1-colmap/Dockerfile.base`.

Fix:

```bash
bash deploy_app1_colmap.sh --base
```

or rerun:

```bash
bash build_and_deploy.sh --base
```

### `kubectl` works only with `sudo`

Make sure kubeconfig is copied and owned by your user:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER":"$USER" ~/.kube/config
export KUBECONFIG=~/.kube/config
```

### GPU is not visible in pods

Check the host first:

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.4.1-runtime-ubuntu22.04 nvidia-smi
```

Then check Kubernetes:

```bash
kubectl describe node | grep -A3 nvidia.com/gpu
kubectl describe pod -n kafka -l app=colmap
kubectl describe pod -n kafka -l app=ia
```

Inside the worker containers, COLMAP GPU indices are relative to the devices exposed through `CUDA_VISIBLE_DEVICES`. If only one GPU is visible to the pod, the only valid index is `0` even if the host labels that GPU as `1`. The current app1 defaults already normalize feature-extraction, matching, bundle-adjustment, and MVS GPU indices to this container-local numbering.

### The dashboard opens but no data is visible

Check the services and logs:

```bash
kubectl get svc -n kafka
kubectl logs deployment/dashboard-api -n kafka
kubectl logs deployment/dashboard-frontend -n kafka
kubectl logs deployment/kafka-broker -n kafka
```

### The workers do not see the dataset

The manifest mounts the host root at `/host` and expects the real dataset on the host at `/mnt/j/workspace`.

Validate from the host:

```bash
ls /mnt/j/workspace
```

Validate from a pod:

```bash
kubectl exec -n kafka deployment/processing-worker -- ls /host/mnt/j/workspace
```

## Clean reinstall

To rebuild and redeploy everything from the repository state:

```bash
bash build_and_deploy.sh --base
```

To remove the deployed objects first:

```bash
kubectl delete namespace kafka
```

Then rerun:

```bash
bash build_and_deploy.sh --base
```

## Acknowledgements

This pipeline builds on a substantial amount of upstream open-source work. In particular, thanks to the maintainers and contributors of:

- COLMAP and PyCOLMAP for SfM, MVS, and reconstruction tooling
- LichtFeld-Studio for high-performance C++/CUDA Gaussian Splatting training (MRNF strategy)
- gsplat for differentiable orthographic Gaussian rasterisation (rendering only)
- PyTorch, NVIDIA CUDA, and NVLabs nvdiffrast for GPU-backed inference and rasterization
- Ultralytics YOLO for OBB detection models and tooling
- Meta SAM 3 and the Hugging Face Transformers integration for prompt-based segmentation
- Rasterio, pyproj, and OpenCV for geospatial and image-processing primitives
- SciPy for spatial filtering (cKDTree, connected components)
- Apache Kafka and confluent-kafka-python for event transport between services
- FastAPI for the dashboard API
- Next.js, React, Leaflet, React-Leaflet, lucide, and Tailwind CSS for the dashboard frontend
- Docker, Moby, K3s, and Kubernetes for container build and orchestration infrastructure

## Citation and third-party licensing notes

This section is an operational summary for this repository. It is not legal advice. If you redistribute this project or ship derived images, keep the upstream license texts and notices for the components you bundle.

### COLMAP citation

If you use the COLMAP-based reconstruction parts of this pipeline in research, cite the upstream COLMAP papers:

```bibtex
@inproceedings{schoenberger2016sfm,
  author={Sch\"{o}nberger, Johannes Lutz and Frahm, Jan-Michael},
  title={Structure-from-Motion Revisited},
  booktitle={Conference on Computer Vision and Pattern Recognition (CVPR)},
  year={2016},
}

@inproceedings{schoenberger2016mvs,
  author={Sch\"{o}nberger, Johannes Lutz and Zheng, Enliang and Pollefeys, Marc and Frahm, Jan-Michael},
  title={Pixelwise View Selection for Unstructured Multi-View Stereo},
  booktitle={European Conference on Computer Vision (ECCV)},
  year={2016},
}
```

COLMAP itself is distributed under the new BSD license. If you redistribute binaries or images that include COLMAP, retain the copyright notice, license text, disclaimer, and do not use the ETH Zurich or UNC Chapel Hill names to endorse your product without permission.

### Gaussian Splatting citations

The Gaussian Splatting orthophoto pipeline uses LichtFeld-Studio for training (MRNF strategy) and gsplat for orthographic rendering. The following research is foundational:

```bibtex
@article{kerbl3Dgaussians,
  author={Kerbl, Bernhard and Kopanas, Georgios and Leimk\"uhler, Thomas and Drettakis, George},
  title={3D Gaussian Splatting for Real-Time Radiance Field Rendering},
  journal={ACM Transactions on Graphics},
  volume={42},
  number={4},
  year={2023},
}
```

```bibtex
@article{kheradmand2024mcmc,
  author={Kheradmand, Shakiba and Rebain, Daniel and Sharma, Gopal and Sun, Weiwei and Tseng, Jeff and Isack, Hossam and Kar, Abhishek and Tagliasacchi, Andrea and Yi, Kwang Moo},
  title={3D Gaussian Splatting as Markov Chain Monte Carlo},
  journal={Advances in Neural Information Processing Systems},
  volume={37},
  year={2024},
}
```

```bibtex
@article{ye2024gsplatopensourcelibrarygaussian,
  title={gsplat: An Open-Source Library for Gaussian Splatting},
  author={Vickie Ye and Ruilong Li and Justin Kerr and Matias Turkulainen and Brent Yi and Zhuoyang Pan and Otto Seiskari and Jianbo Ye and Jeffrey Hu and Matthew Tancik and Angjoo Kanazawa},
  journal={Journal of Machine Learning Research},
  year={2025},
  note={Available at https://github.com/nerfstudio-project/gsplat},
}
```

```bibtex
@article{lin2024vastgaussian,
  author={Lin, Jiaqi and Li, Zhihao and Tang, Xiao and He, Jianzhuang and Liu, Shiyong and Liu, Jiayue and Lu, Yangdi and Wu, Xiaofei and Li, Songcen and Qu, Youliang and Dai, Yuxiang},
  title={VastGaussian: Vast 3D Gaussians for Large Scene Reconstruction},
  booktitle={CVPR},
  year={2024},
}
```

```bibtex
@article{wang2024tortho,
  author={Wang, Xin and Qu, Sai and Jiang, Fan and Li, Xinming},
  title={Tortho-Gaussian: Splatting True Digital Orthophoto Maps},
  journal={arXiv preprint arXiv:2411.19594},
  year={2024},
}
```

### Direct dependencies used by this repository

| Technology | Used in this repo | Upstream license | Practical note for redistribution |
| --- | --- | --- | --- |
| COLMAP / PyCOLMAP | `app1-colmap` | BSD 3-Clause | Keep license and copyright notices in source or binary distributions. |
| LichtFeld-Studio | `app1-colmap` (Gaussian Splatting training, MRNF strategy) | See LichtFeld-Studio LICENSE | Review the upstream license before redistribution. |
| gsplat | `app1-colmap` (orthographic Gaussian rasterisation, rendering only) | Apache 2.0 | Keep license notices. |
| Docker CLI / Moby | host install and image builds | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| K3s | local cluster runtime | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| Kubernetes | orchestration API/runtime used through K3s | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| Apache Kafka | broker image in `kafka-local.yaml` | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| confluent-kafka-python | Python services | Apache 2.0 for the source distribution | Keep the package license and bundled third-party notices if you redistribute it. |
| PyTorch | `app1-colmap`, `app2-ia` | BSD-style permissive license | Keep the upstream license text and notices. |
| nvdiffrast | `app1-colmap` | NVIDIA Source Code License (1-Way Commercial) | Review NVIDIA's license before redistribution; it is not a standard MIT/BSD license. |
| Ultralytics YOLO | `app2-ia` | AGPL-3.0 or commercial enterprise license | This is the main license-sensitive dependency in the ML stack. For private or proprietary deployment, review Ultralytics terms carefully and obtain the appropriate license if needed. |
| Meta SAM 3 code (`facebookresearch/sam3`) | `app2-ia` runtime path and local utility scripts | SAM License according to the upstream GitHub repository | Review the upstream SAM License directly before redistribution or commercial packaging; this is not a standard permissive OSS license. |
| Hugging Face gated model `facebook/sam3` | `app2-ia` runtime model download | Hugging Face model card shows `License: other` and gated access terms | Treat the checkpoint separately from the source repo: access requires approval, contact-sharing conditions, and compliance with the model card terms plus the upstream SAM License. |
| Rasterio | `app1-colmap`, `app2-ia`, `app3-processing` | BSD 3-Clause | Keep copyright and disclaimer notices. |
| pyproj | `app1-colmap`, `app2-ia` | MIT | Keep the license and copyright notice. |
| OpenCV | `app1-colmap`, `app3-processing` | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| FastAPI | `app4-dashboard/api` | MIT | Keep the license and copyright notice. |
| Next.js | `app4-dashboard/frontend` | MIT | Keep the license and copyright notice. |
| React / React DOM | `app4-dashboard/frontend` | MIT | Keep the license and copyright notice. |
| Leaflet | `app4-dashboard/frontend` | BSD 2-Clause | Keep copyright and disclaimer notices. |
| React-Leaflet | `app4-dashboard/frontend` | Hippocratic License 2.1 | Review this license explicitly before redistribution. It is not a standard permissive OSS license and includes additional conditions beyond notice preservation. |
| lucide / lucide-react | `app4-dashboard/frontend` | ISC, with MIT notice for Feather-derived portions | Keep both upstream notices when redistributing bundled copies. |
| Tailwind CSS | `app4-dashboard/frontend` build tooling | MIT | Keep the license and copyright notice. |

### Important license-sensitive items

For this repository, the dependencies that deserve explicit review before any redistribution or commercial packaging are:

1. `ultralytics`
2. `facebookresearch/sam3` and the gated `facebook/sam3` model distribution
3. `nvdiffrast`
4. `react-leaflet`

Those items are not routine MIT/BSD-only cases. If you want a fully permissive redistribution story, review them first and replace them if their terms do not fit your use case.

### Docker note

This installation guide targets Docker Engine on Linux. If you choose to use Docker Desktop instead, review Docker Desktop's separate product and subscription terms yourself; they are outside the scope of this repository.

### Base images and transitive dependencies

The application images also inherit licenses from their base images and system packages, including Ubuntu, Python, Node.js, NVIDIA CUDA images, and package-manager-installed dependencies. If you redistribute built images, generate and ship a complete third-party notice bundle for the exact image digests you publish, not just the top-level projects listed above.