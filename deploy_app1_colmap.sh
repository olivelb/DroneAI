#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
    cat <<'EOF'
Usage: ./deploy_app1_colmap.sh [--base] [--no-restart] [--help]

Options:
  --base        Force a rebuild from scratch. Rebuild the COLMAP+LichtFeld base image
                and rebuild the app image with Docker cache disabled.
  --no-restart  Build and import the image into k3s without restarting the deployment.
  --help        Show this help message.
EOF
}

DOCKER_BUILD_FLAGS=()

for arg in "$@"; do
    case "$arg" in
        --base)
            BUILD_BASE=1
            ;;
        --no-restart)
            RESTART_DEPLOYMENT=0
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ "$BUILD_BASE" -eq 1 ]]; then
    DOCKER_BUILD_FLAGS+=(--no-cache)
fi

# Build base image only if it doesn't exist yet (or pass --base to force rebuild)
# This includes COLMAP, Ceres, PyTorch, gsplat, and LichtFeld-Studio in one multi-stage build.
# BuildKit builds the 3 independent stages (colmap, pip, lichtfeld) in parallel.
if [[ "$BUILD_BASE" -eq 1 ]] || ! sudo docker image inspect drone-colmap-base:latest &>/dev/null; then
    echo "🛠️ Building base image (COLMAP + LichtFeld + Python deps)... This is slow but only needed once."
    export DOCKER_BUILDKIT=1
    sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap-base:latest -f app1-colmap/Dockerfile.base .
    echo "✅ Base image ready (Docker-only, not imported to k3s)."
fi

echo "🛠️ Building app image (just the Python code)..."
export DOCKER_BUILDKIT=1
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap:latest -f app1-colmap/Dockerfile .

# Clean up Docker build cache to free disk before the large k3s import
echo "🧹 Cleaning Docker build cache..."
sudo docker builder prune -f --filter 'until=1h' 2>/dev/null || true
sudo docker image prune -f 2>/dev/null || true

echo "📦 Importing app image to k3s..."
sudo docker save drone-colmap:latest | sudo k3s ctr images import -
echo "🧩 Applying Kubernetes manifest to keep colmap-worker resources in sync..."
sudo kubectl apply -f kafka-local.yaml
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
    echo "🚀 Restarting colmap-worker deployment..."
    sudo kubectl rollout restart deployment colmap-worker -n kafka
    sudo kubectl rollout status deployment colmap-worker -n kafka --timeout=10m
    echo "✅ App 1 (COLMAP) deployed!"
else
    echo "✅ App 1 image staged in k3s; deployment not restarted."
fi
