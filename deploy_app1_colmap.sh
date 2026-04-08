#!/bin/bash
set -euo pipefail

BUILD_BASE=0
BUILD_LICHTFELD=0
RESTART_DEPLOYMENT=1

usage() {
    cat <<'EOF'
Usage: ./deploy_app1_colmap.sh [--base] [--lichtfeld] [--no-restart] [--help]

Options:
  --base        Force a rebuild from scratch. Rebuild the COLMAP base image and
                rebuild the app image with Docker cache disabled.
  --lichtfeld   Force a rebuild of the LichtFeld-Studio runtime image.
                Only needed when upgrading LichtFeld-Studio itself.
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
        --lichtfeld)
            BUILD_LICHTFELD=1
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

# Build LichtFeld runtime image if it doesn't exist yet (or pass --lichtfeld to force rebuild)
if [[ "$BUILD_LICHTFELD" -eq 1 ]] || ! sudo docker image inspect lichtfeld-runtime:latest &>/dev/null; then
    echo "🛠️ Building LichtFeld-Studio runtime image (CUDA 12.8 + vcpkg + build)..."
    echo "   This is slow (~30-45 min) but only needed once per LichtFeld version."
    export DOCKER_BUILDKIT=1
    sudo docker build "${DOCKER_BUILD_FLAGS[@]}" --progress=plain \
        -t lichtfeld-runtime:latest \
        -f app1-colmap/Dockerfile.lichtfeld .
    echo "📦 Importing LichtFeld runtime image to k3s..."
    sudo docker save lichtfeld-runtime:latest | sudo k3s ctr images import -
    echo "✅ LichtFeld runtime image ready."
fi

# Build base image only if it doesn't exist yet (or pass --base to force rebuild)
if [[ "$BUILD_BASE" -eq 1 ]] || ! sudo docker image inspect drone-colmap-base:latest &>/dev/null; then
    echo "🛠️ Building base image (CUDA + COLMAP + deps)... This is slow but only needed once."
    export DOCKER_BUILDKIT=1
    sudo docker build "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap-base:latest -f app1-colmap/Dockerfile.base .
    echo "📦 Importing base image to k3s..."
    sudo docker save drone-colmap-base:latest | sudo k3s ctr images import -
    echo "✅ Base image ready."
fi

echo "🛠️ Building app image (just the Python code)..."
export DOCKER_BUILDKIT=1
sudo docker build "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap:latest -f app1-colmap/Dockerfile .
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
