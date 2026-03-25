#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🛠️ Building Drone Dashboard API..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-api:latest > drone-dashboard-api.tar
sudo k3s ctr images import drone-dashboard-api.tar
rm drone-dashboard-api.tar
echo "🧩 Applying Kubernetes manifests to keep dashboard-api config and RBAC in sync..."
sudo kubectl apply -f kafka-local.yaml
sudo kubectl apply -f dashboard-api-rbac.yaml
echo "🚀 Restarting dashboard-api deployment..."
sudo kubectl rollout restart deployment dashboard-api -n kafka
sudo kubectl rollout status deployment dashboard-api -n kafka --timeout=10m
echo "✅ App 4 (Dashboard API) deployed!"
