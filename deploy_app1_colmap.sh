#!/bin/bash
set -e

BUILD_BASE=0
RESTART_DEPLOYMENT=1

for arg in "$@"; do
    case "$arg" in
        --base)
            BUILD_BASE=1
            ;;
        --no-restart)
            RESTART_DEPLOYMENT=0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 [--base] [--no-restart]" >&2
            exit 1
            ;;
    esac
done

# Build base image only if it doesn't exist yet (or pass --base to force rebuild)
if [[ "$BUILD_BASE" -eq 1 ]] || ! sudo docker image inspect drone-colmap-base:latest &>/dev/null; then
    echo "🛠️ Building base image (CUDA + COLMAP + deps)... This is slow but only needed once."
    export DOCKER_BUILDKIT=1
    sudo docker build --progress=plain -t drone-colmap-base:latest -f app1-colmap/Dockerfile.base .
    echo "📦 Importing base image to k3s..."
    sudo docker save drone-colmap-base:latest | sudo k3s ctr images import -
    echo "✅ Base image ready."
fi

echo "🛠️ Building app image (just the Python code)..."
export DOCKER_BUILDKIT=1
sudo docker build --progress=plain -t drone-colmap:latest -f app1-colmap/Dockerfile .
echo "📦 Importing app image to k3s..."
sudo docker save drone-colmap:latest | sudo k3s ctr images import -
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
    echo "🚀 Restarting colmap-worker deployment..."
    sudo kubectl rollout restart deployment colmap-worker -n kafka
    echo "✅ App 1 (COLMAP) deployed!"
else
    echo "✅ App 1 image staged in k3s; deployment not restarted."
fi
