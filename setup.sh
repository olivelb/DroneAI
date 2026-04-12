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
if ! dpkg -l | grep -q nvidia-container-toolkit; then
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
if ! nvidia-smi &>/dev/null; then
    echo "❌ nvidia-smi failed. Install NVIDIA drivers on the host before proceeding."
    exit 1
fi
echo "✅ GPU detected: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"

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
echo "Check your pods with: kubectl get pods -n kafka"
echo "========================================================"
