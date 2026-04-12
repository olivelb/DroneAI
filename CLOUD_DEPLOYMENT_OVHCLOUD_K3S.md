# DroneAI Cloud Deployment Guide

This document explains how to deploy the full DroneAI pipeline on a French cloud provider with GPU support.

Recommended target:

- Provider: OVHcloud
- Region: `GRA` (Gravelines, France) or `SBG` (Strasbourg, France)
- Kubernetes distribution: self-managed K3s
- GPU strategy: dedicated GPU worker nodes for `app1-colmap` and `app2-ia`
- Shared storage strategy: NFS mounted on every node at `/mnt/j/workspace`

This is the best fit for the current repository because the codebase already assumes:

- K3s-style deployment scripts
- host-visible paths mounted under `/host`
- a shared workspace rooted at `/mnt/j/workspace`
- GPU-backed `colmap-worker` and `ia-worker`

Managed Kubernetes is possible later, but the current repository is closer to a self-managed K3s cluster with a shared filesystem than to a stateless managed-Kubernetes design.

## What You Are Deploying

The pipeline contains five services:

1. `app1-colmap`: COLMAP reconstruction and orthomosaic generation
2. `app2-ia`: AI inference on tiles, GPU-backed
3. `app3-processing`: tiling, aggregation, annotated orthomosaic generation
4. `app4-dashboard/api`: control and status API
5. `app4-dashboard/frontend`: web UI

Supporting components:

- Kafka broker
- shared mission workspace
- Hugging Face token secret for app2 when using SAM 3
- GPU scheduling support inside Kubernetes

## Recommended Cloud Architecture

Use four nodes for the first serious cloud deployment.

1. `cp-1`: control-plane and general CPU workloads
2. `gpu-colmap-1`: dedicated GPU node for `app1-colmap`
3. `gpu-ia-1`: dedicated GPU node for `app2-ia`
4. `storage-1`: NFS server for `/mnt/j/workspace`

You can collapse this to three nodes by hosting NFS on the control-plane node, but separating storage is cleaner.

### Why two GPU nodes

The current manifests keep both GPU deployments alive at the same time:

- `colmap-worker` requests `nvidia.com/gpu: 1`
- `ia-worker` requests `nvidia.com/gpu: 1`

If you only have one visible GPU in the cluster, one of those pods will remain pending unless you manually scale services up and down.

## Sizing Guidance

Choose actual OVH flavors based on current availability, but use these resource targets.

### Control-plane / CPU node

- 8 to 16 vCPU
- 16 to 32 GiB RAM
- 100+ GiB SSD

Runs:

- K3s server
- Kafka
- processing worker
- dashboard API
- dashboard frontend
- ingress controller
- cert-manager

### COLMAP GPU node

- 1 NVIDIA GPU
- 16+ vCPU
- 96 to 128 GiB RAM preferred
- fast SSD

Why large RAM:

- the current manifest requests up to `80Gi` memory for `colmap-worker`
- COLMAP dense reconstruction and TrueOrtho are the heaviest stage in the pipeline

### IA GPU node

- 1 NVIDIA GPU
- 8+ vCPU
- 16 to 32 GiB RAM

### NFS storage node

- 4+ vCPU
- 8 to 16 GiB RAM
- large attached volume sized for missions, models, and outputs

If your datasets are large, size storage for:

- raw images
- clean copied images
- COLMAP sparse/dense workspaces
- orthomosaics and annotated orthomosaics
- tiles and intermediate outputs

## Big Constraint: Shared Filesystem

The current repo is not fully cloud-native.

It relies on this model:

- host filesystem mounted inside containers at `/host`
- mission workspace expected on the host at `/mnt/j/workspace`

Because of this, all nodes that can run the workers must see the same files at the same absolute host path.

The simplest way to preserve that behavior is:

1. create an NFS server
2. export a directory like `/srv/droneai/workspace`
3. mount it on every K3s node at `/mnt/j/workspace`

That lets the existing application path logic continue to work with minimal code change.

## High-Level Deployment Plan

1. Create OVHcloud project, instances, networking, and DNS
2. Install NVIDIA drivers and container runtime on GPU nodes
3. Install and join the K3s cluster
4. Install NFS server and mount `/mnt/j/workspace` on all nodes
5. Install Kubernetes add-ons: ingress, cert-manager, NVIDIA device plugin
6. Push your Docker images to a registry
7. Adapt manifests from local K3s to cloud K3s
8. Create secrets and deploy
9. Validate GPU scheduling, storage visibility, dashboard access, and mission flow
10. Add backups, monitoring, and operational hardening

## Step 1: Prepare OVHcloud

### 1.1 Create the cloud project

In OVHcloud Public Cloud:

1. Create a project for DroneAI
2. Select a French region such as `GRA` if GPU flavors are available there
3. If a GPU flavor is not available in your preferred French region, use the closest region that offers it and document the data residency tradeoff

### 1.2 Create instances

Provision these Linux instances:

- `cp-1` for control-plane and CPU workloads
- `gpu-colmap-1` for app1
- `gpu-ia-1` for app2
- `storage-1` for NFS

Use Ubuntu 24.04 LTS on all nodes to match the current Dockerfiles (all stages use Ubuntu 24.04).

### 1.3 Create networking rules

Allow at minimum:

- SSH from your admin IP
- TCP 6443 between nodes for K3s API
- flannel / CNI traffic between nodes
- TCP 80 and 443 from the Internet to the ingress endpoint
- optional TCP 22 between nodes for admin tasks

If you use a cloud load balancer, allow it to forward 80 and 443 to your ingress node(s).

### 1.4 Reserve DNS names

Recommended DNS names:

- `droneai.example.fr` for the frontend
- `api.droneai.example.fr` for the dashboard API

Point them later to your cloud load balancer public IP.

## Step 2: Prepare All Servers

Run these steps on all nodes unless otherwise stated.

### 2.1 System packages

```bash
sudo apt-get update
sudo apt-get install -y \
  curl wget git jq unzip ca-certificates \
  apt-transport-https software-properties-common \
  nfs-common
```

Also install Docker because the repository build flow already assumes it.

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
rm get-docker.sh
sudo usermod -aG docker "$USER"
newgrp docker
```

### 2.2 GPU nodes only: NVIDIA driver and toolkit

On `gpu-colmap-1` and `gpu-ia-1`:

1. Install the recommended NVIDIA driver for the chosen OVH GPU flavor
2. Confirm `nvidia-smi` works
3. Install the NVIDIA container toolkit

Example:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Validate:

```bash
nvidia-smi
sudo docker run --rm --gpus all nvidia/cuda:12.8.1-runtime-ubuntu24.04 nvidia-smi
```

## Step 3: Build the Shared Workspace

### 3.1 Configure NFS server on `storage-1`

Install NFS server:

```bash
sudo apt-get update
sudo apt-get install -y nfs-kernel-server
```

Create exported directories:

```bash
sudo mkdir -p /srv/droneai/workspace
sudo chown -R $USER:$USER /srv/droneai/workspace
```

Export them:

```bash
echo '/srv/droneai/workspace *(rw,sync,no_subtree_check,no_root_squash)' | sudo tee -a /etc/exports
sudo exportfs -rav
sudo systemctl enable --now nfs-server
```

### 3.2 Mount the NFS share on every node

On every K3s node:

```bash
sudo mkdir -p /mnt/j/workspace
```

Add to `/etc/fstab`:

```fstab
storage-1:/srv/droneai/workspace /mnt/j/workspace nfs defaults,_netdev 0 0
```

Mount:

```bash
sudo mount -a
df -h | grep /mnt/j/workspace
```

This path is important because the repo currently defaults to `WORKSPACE_DIR=/mnt/j/workspace` and mounts the host root to `/host` inside containers.

## Step 4: Install K3s Cluster

### 4.1 Install the K3s server on `cp-1`

```bash
curl -sfL https://get.k3s.io | sh -s - --write-kubeconfig-mode 644
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER":"$USER" ~/.kube/config
export KUBECONFIG=~/.kube/config
kubectl get nodes
```

Get the join token:

```bash
sudo cat /var/lib/rancher/k3s/server/node-token
```

### 4.2 Join worker nodes

On `gpu-colmap-1`, `gpu-ia-1`, and optionally `storage-1` if you want it schedulable:

```bash
curl -sfL https://get.k3s.io | K3S_URL=https://<CP_PUBLIC_OR_PRIVATE_IP>:6443 K3S_TOKEN=<TOKEN> sh -
```

If `storage-1` should never run workloads, do not join it to the cluster.

### 4.3 Label nodes

From the control-plane:

```bash
kubectl label node gpu-colmap-1 workload=colmap gpu=true
kubectl label node gpu-ia-1 workload=ia gpu=true
kubectl label node cp-1 workload=cpu
```

If you want to keep GPU nodes exclusive:

```bash
kubectl taint node gpu-colmap-1 gpu=colmap:NoSchedule
kubectl taint node gpu-ia-1 gpu=ia:NoSchedule
```

You will then need matching tolerations in the manifests.

## Step 5: Install Cluster Add-ons

### 5.1 Install ingress-nginx

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
kubectl get pods -n ingress-nginx
```

### 5.2 Install cert-manager

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.17.1/cert-manager.yaml
kubectl get pods -n cert-manager
```

Create a Let’s Encrypt issuer later once ingress is ready.

### 5.3 Install NVIDIA device plugin

Use Helm or the static manifest. Helm example:

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
helm upgrade --install nvidia-device-plugin nvdp/nvidia-device-plugin \
  --namespace nvidia-device-plugin \
  --create-namespace
```

Validate:

```bash
kubectl get nodes -o json | jq '.items[].status.allocatable'
kubectl describe node gpu-colmap-1 | grep -A3 nvidia.com/gpu
kubectl describe node gpu-ia-1 | grep -A3 nvidia.com/gpu
```

## Step 6: Choose an Image Registry

The current local scripts build images and import them directly into local K3s. In the cloud, use a registry.

Recommended options:

- GitHub Container Registry
- Docker Hub
- OVH-compatible private registry if you already operate one

For simplicity, this guide uses GitHub Container Registry.

Set image names like:

- `ghcr.io/<org>/drone-colmap:latest`
- `ghcr.io/<org>/drone-ia:latest`
- `ghcr.io/<org>/drone-processing:latest`
- `ghcr.io/<org>/drone-dashboard-api:latest`
- `ghcr.io/<org>/drone-dashboard-frontend:latest`

Build and push:

```bash
docker login ghcr.io

docker build -t ghcr.io/<org>/drone-colmap-base:latest -f app1-colmap/Dockerfile.base .
docker push ghcr.io/<org>/drone-colmap-base:latest

docker build -t ghcr.io/<org>/drone-colmap:latest -f app1-colmap/Dockerfile .
docker build -t ghcr.io/<org>/drone-ia:latest -f app2-ia/Dockerfile .
docker build -t ghcr.io/<org>/drone-processing:latest -f app3-processing/Dockerfile .
docker build -t ghcr.io/<org>/drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .
docker build -t ghcr.io/<org>/drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .

docker push ghcr.io/<org>/drone-colmap:latest
docker push ghcr.io/<org>/drone-ia:latest
docker push ghcr.io/<org>/drone-processing:latest
docker push ghcr.io/<org>/drone-dashboard-api:latest
docker push ghcr.io/<org>/drone-dashboard-frontend:latest
```

If your registry is private, create an image pull secret in Kubernetes.

## Step 7: Adapt the Kubernetes Manifests

Do not apply `kafka-local.yaml` unchanged in the cloud.

You need a cloud variant such as `kafka-cloud-ovh.yaml`.

### 7.1 Required manifest changes

#### Replace local image names

Change:

- `drone-colmap:latest`
- `drone-ia:latest`
- `drone-processing:latest`
- `drone-dashboard-api:latest`
- `drone-dashboard-frontend:latest`

to your registry-backed images.

#### Keep `/host` mount only if you keep the current filesystem model

If you follow this guide, every node mounts the same NFS path at `/mnt/j/workspace`, so mounting `/` as `/host` still works.

That means the current path logic continues to work:

- host path: `/mnt/j/workspace/...`
- inside container: `/host/mnt/j/workspace/...`

This is not elegant, but it is the minimum-change cloud migration path.

#### Replace NodePort services

Change dashboard services from `NodePort` to either:

- `LoadBalancer`, or
- `ClusterIP` plus `Ingress`

Recommended:

- API service: `ClusterIP`
- Frontend service: `ClusterIP`
- one Ingress resource handling both hostnames

#### Pin GPU workloads to the right nodes

Add `nodeSelector` or node affinity:

- `colmap-worker` to `workload=colmap`
- `ia-worker` to `workload=ia`

If you used taints, add tolerations too.

#### Keep the current workspace environment variables

The repo currently expects:

- `WORKSPACE_DIR=/mnt/j/workspace`
- dashboard API `INPUT_DIR=/host/mnt/j/workspace`

Do not change these unless you also refactor the application path model.

### 7.2 Strongly recommended cloud manifest additions

Add these even if they are not in the current local manifest.

- readiness probes
- liveness probes
- `imagePullSecrets` if registry is private
- explicit `nodeSelector` for GPU pods
- resource requests on all pods
- `persistentVolumeClaim` or stable host path for Hugging Face cache if app2 may move between nodes

## Step 8: Prepare Secrets

### 8.1 Hugging Face token

Create the existing required secret:

```bash
export HF_TOKEN=<your_token>
kubectl create namespace kafka --dry-run=client -o yaml | kubectl apply -f -
kubectl -n kafka create secret generic hf-token --from-literal=HF_TOKEN="$HF_TOKEN"
```

### 8.2 Optional image pull secret

If your registry is private:

```bash
kubectl -n kafka create secret docker-registry regcred \
  --docker-server=ghcr.io \
  --docker-username=<user> \
  --docker-password=<token> \
  --docker-email=<email>
```

## Step 9: Add TLS and Ingress

Create a ClusterIssuer for Let’s Encrypt.

Example:

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    email: you@example.fr
    server: https://acme-v02.api.letsencrypt.org/directory
    privateKeySecretRef:
      name: letsencrypt-prod
    solvers:
    - http01:
        ingress:
          class: nginx
```

Then create an ingress exposing:

- `droneai.example.fr` -> frontend
- `api.droneai.example.fr` -> dashboard API

## Step 10: Deploy the Stack

Apply your adapted manifests:

```bash
kubectl apply -f kafka-cloud-ovh.yaml
kubectl apply -f dashboard-api-rbac.yaml
kubectl apply -f ingress-droneai.yaml
```

Watch rollout:

```bash
kubectl get pods -n kafka -w
```

## Step 11: Validate the Cloud Deployment

### 11.1 Cluster-level validation

```bash
kubectl get nodes
kubectl get pods -n kafka -o wide
kubectl get svc -n kafka
kubectl get ingress -n kafka
```

### 11.2 GPU validation

```bash
kubectl describe pod -n kafka -l app=colmap
kubectl describe pod -n kafka -l app=ia
kubectl describe node gpu-colmap-1 | grep -A3 nvidia.com/gpu
kubectl describe node gpu-ia-1 | grep -A3 nvidia.com/gpu
```

### 11.3 Filesystem validation

Verify all worker nodes see the same workspace:

```bash
kubectl exec -n kafka deployment/dashboard-api -- ls /host/mnt/j/workspace
kubectl exec -n kafka deployment/processing-worker -- ls /host/mnt/j/workspace
kubectl exec -n kafka deployment/colmap-worker -- ls /host/mnt/j/workspace
```

### 11.4 Kafka validation

```bash
kubectl logs deployment/kafka-broker -n kafka
kubectl logs deployment/dashboard-api -n kafka
kubectl logs deployment/colmap-worker -n kafka
kubectl logs deployment/processing-worker -n kafka
kubectl logs deployment/ia-worker -n kafka
```

### 11.5 Dashboard validation

Open:

- `https://droneai.example.fr`
- `https://api.droneai.example.fr/docs`

## Step 12: Upload Mission Data

Because the current app design reads from a shared filesystem, you must place mission inputs on the NFS-backed workspace.

Example:

```bash
mkdir -p /mnt/j/workspace/mission_001
rsync -av ./my-photos/ /mnt/j/workspace/mission_001/
```

If you upload from your laptop, copy data to the NFS server first, or to any node where `/mnt/j/workspace` is mounted.

## Step 13: First Production Mission Test

Run a controlled validation mission before real usage.

Checklist:

1. Submit a small mission from the dashboard
2. Confirm `database.db`, `dense/`, and `orthomosaic.tif` appear in the mission workspace
3. Confirm tiles appear under `tiles/<vol_id>/`
4. Confirm `orthomosaic_annotated.tif` is produced
5. Confirm frontend status updates stream correctly

## What You Should Improve After the First Cloud Deployment

This guide gets the current repo running in the cloud with minimum application changes. It is not the end state.

### Priority improvements

1. Replace host-root `/host` mounts with real PVCs or object-storage-backed workflows.
2. Replace the single in-cluster Kafka broker with a more durable deployment or managed Kafka.
3. Move secrets to a managed secret backend.
4. Add backups for `/mnt/j/workspace` and Hugging Face cache if needed.
5. Add monitoring with Prometheus and Grafana.
6. Add centralized logs.
7. Add CI/CD to build and push images automatically.

## Minimal Manifest Checklist for This Repo

Before you call the cloud deployment done, confirm all of the following.

- images pull from a registry, not local Docker daemon state
- all nodes that can run workers see `/mnt/j/workspace`
- GPU nodes expose `nvidia.com/gpu`
- `colmap-worker` and `ia-worker` are pinned to the correct GPU nodes
- dashboard is exposed through Ingress or LoadBalancer, not only NodePort
- `hf-token` secret exists in namespace `kafka`
- dashboard API still sees `INPUT_DIR=/host/mnt/j/workspace`
- app1 still sees `WORKSPACE_DIR=/mnt/j/workspace`

## Cost-Aware Variant

If budget matters, use this reduced setup:

- 1 control-plane / CPU node
- 1 GPU node with enough RAM for COLMAP
- 1 NFS storage node

But then you must accept one of these compromises:

1. only run `app1-colmap` and `app2-ia` sequentially
2. scale `ia-worker` to zero during reconstruction-heavy windows
3. use GPU time-slicing and accept reduced isolation and harder debugging

For the first real cloud deployment, two GPU nodes are simpler and more reliable.

## Final Recommendation

If your goal is to get this repository into the cloud with the least disruption, do this:

1. choose OVHcloud Public Cloud in a French region
2. deploy self-managed K3s, not managed Kubernetes first
3. add one shared NFS mount at `/mnt/j/workspace` on every node
4. use two GPU nodes so app1 and app2 can coexist
5. push images to a registry and maintain a dedicated cloud manifest file

That path respects the current codebase instead of pretending it is already stateless and cloud-native.

If you want, the next step is to create the actual cloud manifest set:

- `kafka-cloud-ovh.yaml`
- `ingress-droneai.yaml`
- optional `registry-secret.yaml` template
- optional `cluster-issuer.yaml`

Those files would let you deploy this architecture directly instead of translating from the local manifest by hand.