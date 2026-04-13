#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================================"
echo "🚀 DroneAI Pipeline - Automated Setup for WSL2 / Ubuntu"
echo "========================================================"

# 1. Update and install basic dependencies
echo "📦 Updating system and installing dependencies..."
sudo apt-get update
sudo apt-get install -y curl wget git unzip jq apt-transport-https ca-certificates software-properties-common

# 2. Install Docker
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo usermod -aG docker $USER
    rm get-docker.sh
    echo "✅ Docker installed."
else
    echo "✅ Docker is already installed."
fi

# 3. Install NVIDIA Container Toolkit (Crucial for GPU in WSL2)
if ! command -v nvidia-ctk &>/dev/null; then
    echo "🟢 Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg \
      && curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    echo "✅ NVIDIA Container Toolkit installed."
else
    echo "✅ NVIDIA Container Toolkit is already installed."
fi

# 4. Install K3s (Lightweight Kubernetes)
if ! command -v k3s &> /dev/null; then
    echo "☸️ Installing K3s..."
    curl -sfL https://get.k3s.io | sh -
    echo "Waiting for K3s to start..."
    sleep 10
    sudo chmod 644 /etc/rancher/k3s/k3s.yaml
    echo "✅ K3s installed."
else
    echo "✅ K3s is already installed."
fi

# Ensure kubectl is accessible
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER:$USER ~/.kube/config
export KUBECONFIG=~/.kube/config
if ! grep -q 'export KUBECONFIG=~/.kube/config' ~/.bashrc 2>/dev/null; then
    echo "export KUBECONFIG=~/.kube/config" >> ~/.bashrc
fi

# 5. Validate GPU access
echo "🔍 Checking GPU access..."
# WSL2 puts nvidia-smi in /usr/lib/wsl/lib/ which may not be in PATH
if ! nvidia-smi &>/dev/null; then
    if [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
        export PATH="$PATH:/usr/lib/wsl/lib"
        echo "   Added /usr/lib/wsl/lib to PATH (WSL2 GPU driver passthrough)"
    else
        echo "❌ nvidia-smi failed. Install NVIDIA drivers on the host before proceeding."
        exit 1
    fi
fi
echo "✅ GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"

# 5b. Install Helm
if ! command -v helm &>/dev/null; then
    echo "⎈ Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
    echo "✅ Helm installed."
else
    echo "✅ Helm is already installed."
fi

# 5c. Install NVIDIA device plugin with GPU time-slicing (2 replicas)
echo "🎮 Setting up NVIDIA device plugin for GPU scheduling..."
if ! sudo helm repo list 2>/dev/null | grep -q nvdp; then
    sudo helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
fi
sudo helm repo update
sudo helm upgrade --install nvidia-device-plugin nvdp/nvidia-device-plugin \
    --namespace nvidia-device-plugin --create-namespace \
    -f "$SCRIPT_DIR/nvdp-values.yaml"
echo "✅ NVIDIA device plugin installed (GPU time-slicing enabled)."

# 5d. Create persistent data directories
echo "📂 Creating persistent data directories..."
DATA_ROOT="/mnt/j/droneAI_workspace"
if [ -d "/mnt/j" ]; then
    mkdir -p "$DATA_ROOT"/{minio-data,postgres-data,kafka-data,model-cache}
    echo "✅ Data directories created under $DATA_ROOT"
else
    echo "⚠️  /mnt/j does not exist — persistent data directories not created."
    echo "   If using a different path, edit charts/drone-ai/values.yaml before deploying."
fi

# 5e. Set up HF token secret
# Note: the drone-ai namespace is created by Helm (--create-namespace) in build_and_deploy.sh.
# We only need to handle the hf-token secret here. build_and_deploy.sh checks this too,
# but we prepare it early so the build script doesn't abort.
echo "🔐 Setting up HF token..."
DEPLOY_NS="drone-ai"
# Create namespace only if it doesn't exist (Helm will adopt it if labels match)
if ! sudo kubectl get namespace "$DEPLOY_NS" >/dev/null 2>&1; then
    sudo kubectl create namespace "$DEPLOY_NS"
    # Label it so Helm can adopt it later
    sudo kubectl label namespace "$DEPLOY_NS" app.kubernetes.io/managed-by=Helm
    sudo kubectl annotate namespace "$DEPLOY_NS" meta.helm.sh/release-name=drone-ai meta.helm.sh/release-namespace=drone-ai
fi
if ! sudo kubectl get secret hf-token -n "$DEPLOY_NS" >/dev/null 2>&1; then
    if [ -n "${HF_TOKEN:-}" ]; then
        sudo kubectl -n "$DEPLOY_NS" create secret generic hf-token --from-literal=HF_TOKEN="$HF_TOKEN"
        echo "✅ hf-token secret created in $DEPLOY_NS namespace."
    elif sudo kubectl get secret hf-token -n kafka >/dev/null 2>&1; then
        echo "   Copying hf-token from kafka namespace..."
        sudo kubectl get secret hf-token -n kafka -o yaml \
            | sed "s/namespace: kafka/namespace: $DEPLOY_NS/" \
            | sudo kubectl apply -f -
        echo "✅ hf-token copied to $DEPLOY_NS namespace."
    else
        echo "⚠️  No HF_TOKEN env var set and no existing secret found."
        echo "   Set it before deploying: export HF_TOKEN=hf_... && sudo kubectl -n drone-ai create secret generic hf-token --from-literal=HF_TOKEN=\"\$HF_TOKEN\""
    fi
else
    echo "✅ hf-token secret already exists."
fi

# 6. Clone external build dependencies (LichtFeld, vcpkg, Ceres, COLMAP)
echo "📥 Preparing external build dependencies..."
bash "$SCRIPT_DIR/setup_deps.sh"

# 7. Build and Deploy the Pipeline
echo "🛠️ Starting the build and deployment process..."
echo "This will compile COLMAP and build all Docker images. It may take some time."
bash "$SCRIPT_DIR/build_and_deploy.sh"

echo "========================================================"
echo "🎉 Installation Complete!"
echo "If Docker says 'permission denied' when running without sudo, please log out and log back in (or run 'newgrp docker')."
echo "Check your pods with: kubectl get pods -n drone-ai"
echo "Frontend: http://localhost:30000"
echo "API:      http://localhost:30080"
echo "MinIO:    http://localhost:30090"
echo "========================================================"
