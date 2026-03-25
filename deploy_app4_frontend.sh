#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🛠️ Building Drone Dashboard Frontend..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-frontend:latest > drone-dashboard-frontend.tar
sudo k3s ctr images import drone-dashboard-frontend.tar
rm drone-dashboard-frontend.tar
echo "🧩 Applying Kubernetes manifest to keep dashboard-frontend config in sync..."
sudo kubectl apply -f kafka-local.yaml
echo "🚀 Restarting dashboard-frontend deployment..."
sudo kubectl rollout restart deployment dashboard-frontend -n kafka
sudo kubectl rollout status deployment dashboard-frontend -n kafka --timeout=10m
echo "✅ App 4 (Dashboard Frontend) deployed!"
