# DroneAI Pipeline

This repository contains a complete local photogrammetry and detection pipeline built from five microservices:

1. `app1-colmap`: COLMAP reconstruction and orthomosaic generation
2. `app2-ia`: YOLO segmentation on image tiles
3. `app3-processing`: tile slicing, detection aggregation, annotated orthomosaic generation
4. `app4-dashboard/api`: FastAPI control and status API
5. `app4-dashboard/frontend`: Next.js dashboard UI

The stack runs locally on Kubernetes via K3s. Kafka is deployed inside the cluster from `kafka-local.yaml`; you do not install Kafka separately on the host.

For runtime architecture, Kafka contracts, orthomosaic construction details, and processing-worker behavior, see `DOCUMENTATION.md`.

## What is required in the repo

For a working installation, these files are part of the install path and must be present:

- `app1-colmap/Dockerfile.base`
- `app1-colmap/Dockerfile`
- `app2-ia/Dockerfile`
- `app3-processing/Dockerfile`
- `app4-dashboard/api/Dockerfile`
- `app4-dashboard/frontend/Dockerfile`
- `kafka-local.yaml`
- `build_and_deploy.sh`
- `setup.sh`
- `deploy_app1_colmap.sh`
- `deploy_app2_ia.sh`
- `deploy_app3_processing.sh`
- `deploy_app4_api.sh`
- `deploy_app4_frontend.sh`

The rest of the repository is the application source code used by those images.

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
4. Build the images and apply `kafka-local.yaml`

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

What `build_and_deploy.sh` does:

- builds `drone-colmap-base:latest` if it does not already exist
- imports the base image into K3s
- builds all five application images
- imports all images into K3s containerd
- applies `kafka-local.yaml`
- restarts the deployments in namespace `kafka`

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

## Running a mission

1. Put your images in a mission directory under `/mnt/j/workspace`
2. Open `http://localhost:30000`
3. Browse to the input directory
4. Keep the workspace path set to `/mnt/j/workspace`
5. Start the mission from the dashboard

The pipeline flow is:

1. `app1-colmap` consumes `vols-bruts` and produces `images-ortho`
2. `app3-processing` consumes `images-ortho`, slices tiles, and produces `image-tiles`
3. `app2-ia` consumes `image-tiles` and produces `tile-detections`
4. `app3-processing` consumes `tile-detections` and writes the final annotated orthomosaic
5. `app4-dashboard/api` streams status to the frontend

## Incremental redeploy commands

Use these when only one service changes.

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

Rebuild the processing worker:

```bash
bash deploy_app3_processing.sh
```

Rebuild the dashboard API:

```bash
bash deploy_app4_api.sh
```

Rebuild the dashboard frontend:

```bash
bash deploy_app4_frontend.sh
```

## Troubleshooting

### `drone-colmap-base:latest` not found

The COLMAP worker depends on the base image from `app1-colmap/Dockerfile.base`.

Fix:

```bash
bash deploy_app1_colmap.sh --base
```

or rerun:

```bash
bash build_and_deploy.sh
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
bash build_and_deploy.sh
```

To remove the deployed objects first:

```bash
kubectl delete namespace kafka
```

Then rerun:

```bash
bash build_and_deploy.sh
```

## Acknowledgements

This pipeline builds on a substantial amount of upstream open-source work. In particular, thanks to the maintainers and contributors of:

- COLMAP and PyCOLMAP for SfM, MVS, and reconstruction tooling
- PyTorch, NVIDIA CUDA, and NVLabs nvdiffrast for GPU-backed inference and rasterization
- Ultralytics YOLO for segmentation models and tooling
- Rasterio, pyproj, and OpenCV for geospatial and image-processing primitives
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

### Direct dependencies used by this repository

| Technology | Used in this repo | Upstream license | Practical note for redistribution |
| --- | --- | --- | --- |
| COLMAP / PyCOLMAP | `app1-colmap` | BSD 3-Clause | Keep license and copyright notices in source or binary distributions. |
| Docker CLI / Moby | host install and image builds | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| K3s | local cluster runtime | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| Kubernetes | orchestration API/runtime used through K3s | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| Apache Kafka | broker image in `kafka-local.yaml` | Apache 2.0 | Keep license notices and mark changes if you redistribute modified copies. |
| confluent-kafka-python | Python services | Apache 2.0 for the source distribution | Keep the package license and bundled third-party notices if you redistribute it. |
| PyTorch | `app1-colmap`, `app2-ia` | BSD-style permissive license | Keep the upstream license text and notices. |
| nvdiffrast | `app1-colmap` | NVIDIA Source Code License (1-Way Commercial) | Review NVIDIA's license before redistribution; it is not a standard MIT/BSD license. |
| Ultralytics YOLO | `app2-ia` | AGPL-3.0 or commercial enterprise license | This is the main license-sensitive dependency in the ML stack. For private or proprietary deployment, review Ultralytics terms carefully and obtain the appropriate license if needed. |
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
2. `nvdiffrast`
3. `react-leaflet`

Those three are not routine MIT/BSD-only cases. If you want a fully permissive redistribution story, review them first and replace them if their terms do not fit your use case.

### Docker note

This installation guide targets Docker Engine on Linux. If you choose to use Docker Desktop instead, review Docker Desktop's separate product and subscription terms yourself; they are outside the scope of this repository.

### Base images and transitive dependencies

The application images also inherit licenses from their base images and system packages, including Ubuntu, Python, Node.js, NVIDIA CUDA images, and package-manager-installed dependencies. If you redistribute built images, generate and ship a complete third-party notice bundle for the exact image digests you publish, not just the top-level projects listed above.