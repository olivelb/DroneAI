#!/bin/bash
set -e

cd /home/olivier

echo "=== Rebuilding colmap-worker (full S3 upload + cleanup) ==="
sudo docker build --network=host -t drone-colmap:latest -f app1-colmap/Dockerfile .

echo "=== Importing into k3s ==="
sudo docker save drone-colmap:latest > drone-colmap.tar
sudo k3s ctr images import drone-colmap.tar
rm drone-colmap.tar

echo "=== Restarting colmap-worker pod ==="
kubectl rollout restart deploy/colmap-worker -n drone-ai
kubectl rollout status deploy/colmap-worker -n drone-ai --timeout=120s

echo "=== Done! ==="
echo "Changes:"
echo "  - ALL artifacts now uploaded to S3 (ortho, sparse, dense, gaussian PLY, checkpoints)"
echo "  - Local workspace cleaned up after upload (frees WSL disk)"
echo ""
echo "S3 layout per mission:"
echo "  missions/{vol_id}/"
echo "    orthomosaic.tif"
echo "    orthomosaic.height.tif"
echo "    alignment_transform.json"
echo "    geo_data.txt / geo_data.txt.crs"
echo "    colmap/database.db"
echo "    colmap/sparse/0/  (cameras.bin, images.bin, points3D.bin)"
echo "    colmap/sparse_geo/"
echo "    dense/sparse/0/   (undistorted model)"
echo "    dense/images/     (undistorted images)"
echo "    gaussian/final.ply"
echo "    gaussian/full/splat_*.ply"
echo "    gaussian/full/checkpoints/"
