#!/bin/bash
set -e
echo "🛠️ Building Drone IA Worker..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-ia:latest -f app2-ia/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-ia:latest > drone-ia.tar
sudo k3s ctr images import drone-ia.tar
rm drone-ia.tar
echo "🚀 Restarting ia-worker deployment..."
sudo kubectl rollout restart deployment ia-worker -n kafka
echo "✅ App 2 (IA) deployed!"
