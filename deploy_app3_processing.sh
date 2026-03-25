#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🛠️ Building Drone Processing Worker..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-processing:latest -f app3-processing/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-processing:latest > drone-processing.tar
sudo k3s ctr images import drone-processing.tar
rm drone-processing.tar
echo "🧩 Applying Kubernetes manifest to keep processing-worker config in sync..."
sudo kubectl apply -f kafka-local.yaml
echo "🚀 Restarting processing-worker deployment..."
sudo kubectl rollout restart deployment processing-worker -n kafka
sudo kubectl rollout status deployment processing-worker -n kafka --timeout=10m
echo "✅ App 3 (Processing) deployed!"
