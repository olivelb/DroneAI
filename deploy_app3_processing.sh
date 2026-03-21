#!/bin/bash
set -e
echo "🛠️ Building Drone Processing Worker..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-processing:latest -f app3-processing/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-processing:latest > drone-processing.tar
sudo k3s ctr images import drone-processing.tar
rm drone-processing.tar
echo "🚀 Restarting processing-worker deployment..."
sudo kubectl rollout restart deployment processing-worker -n kafka
echo "✅ App 3 (Processing) deployed!"
