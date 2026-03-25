#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔐 Checking required Kubernetes secrets..."
if ! sudo kubectl get secret hf-token -n kafka >/dev/null 2>&1; then
	echo "❌ Missing secret 'hf-token' in namespace 'kafka'."
	echo "   Create it before deploying App 2, for example:"
	echo '   export HF_TOKEN=your_huggingface_token'
	echo '   sudo kubectl -n kafka create secret generic hf-token --from-literal=HF_TOKEN="$HF_TOKEN"'
	exit 1
fi

echo "🛠️ Building Drone IA Worker..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-ia:latest -f app2-ia/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-ia:latest > drone-ia.tar
sudo k3s ctr images import drone-ia.tar
rm drone-ia.tar
echo "🧩 Applying Kubernetes manifest to keep ia-worker resources and env in sync..."
sudo kubectl apply -f kafka-local.yaml
echo "🚀 Restarting ia-worker deployment..."
sudo kubectl rollout restart deployment ia-worker -n kafka
sudo kubectl rollout status deployment ia-worker -n kafka --timeout=10m
echo "✅ App 2 (IA) deployed!"
