#!/bin/bash
set -e
echo "🛠️ Building Drone Dashboard API..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-api:latest > drone-dashboard-api.tar
sudo k3s ctr images import drone-dashboard-api.tar
rm drone-dashboard-api.tar
echo "🚀 Restarting dashboard-api deployment..."
sudo kubectl rollout restart deployment dashboard-api -n kafka
echo "✅ App 4 (Dashboard API) deployed!"
