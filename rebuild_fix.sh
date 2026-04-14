#!/bin/bash
set -e

cd /home/olivier

echo "=== Rebuilding dashboard-api (browse path fix) ==="
sudo docker build --network=host -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .

echo "=== Rebuilding colmap-worker (prefix normalization fix) ==="
sudo docker build --network=host -t drone-colmap:latest -f app1-colmap/Dockerfile .

echo "=== Importing into k3s ==="
for IMG in drone-dashboard-api drone-colmap; do
    echo "   -> $IMG"
    sudo docker save "$IMG:latest" > "$IMG.tar"
    sudo k3s ctr images import "$IMG.tar"
    rm "$IMG.tar"
done

echo "=== Restarting pods ==="
kubectl rollout restart deploy/dashboard-api deploy/colmap-worker -n drone-ai
kubectl rollout status deploy/dashboard-api deploy/colmap-worker -n drone-ai --timeout=120s

echo "=== Done! ==="
echo "Fixes applied:"
echo "  1. /browse endpoint: directory paths no longer have trailing slash"
echo "  2. COLMAP worker: input_dataset prefix normalized (strips trailing /)"
