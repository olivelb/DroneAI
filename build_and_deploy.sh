#!/bin/bash
# Script de build et déploiement du pipeline DroneAI (Microservices)

set -ex  # -x for command tracing

echo "🛠️ Construction des images microservices..."

# Use --progress=plain for verbose output in logs
export DOCKER_BUILDKIT=1

# 1. COLMAP Worker
echo "   -> Building Drone COLMAP Worker..."
sudo docker build --progress=plain -t drone-colmap:latest -f app1-colmap/Dockerfile .

# 2. IA Worker
echo "   -> Building Drone IA Worker..."
sudo docker build -t drone-ia:latest -f app2-ia/Dockerfile .

# 3. Processing Worker
echo "   -> Building Drone Processing Worker..."
sudo docker build -t drone-processing:latest -f app3-processing/Dockerfile .

# 4. Dashboard API
echo "   -> Building Drone Dashboard API..."
sudo docker build -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .

# 5. Dashboard Frontend
echo "   -> Building Drone Dashboard Frontend..."
sudo docker build -t drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .

echo "📦 Importation des images dans k3s..."

IMAGES=("drone-colmap" "drone-ia" "drone-processing" "drone-dashboard-api" "drone-dashboard-frontend")

for IMG in "${IMAGES[@]}"; do
    echo "   -> Exporting/Importing $IMG..."
    sudo docker save "$IMG:latest" > "$IMG.tar"
    sudo k3s ctr images import "$IMG.tar"
    rm "$IMG.tar"
done

echo "🚀 Déploiement sur Kubernetes..."
sudo kubectl apply -f kafka-local.yaml

# Restart to ensure new images are pulled
sudo kubectl rollout restart deployment -n kafka

echo "✅ Pipeline microservices déployé !"
