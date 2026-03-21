#!/bin/bash
set -e
echo "🛠️ Building Drone Dashboard Frontend..."
export DOCKER_BUILDKIT=1
sudo docker build -t drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-frontend:latest > drone-dashboard-frontend.tar
sudo k3s ctr images import drone-dashboard-frontend.tar
rm drone-dashboard-frontend.tar
echo "🚀 Restarting dashboard-frontend deployment..."
sudo kubectl rollout restart deployment dashboard-frontend -n kafka
echo "✅ App 4 (Dashboard Frontend) deployed!"
