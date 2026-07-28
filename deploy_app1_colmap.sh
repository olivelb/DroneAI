#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
    cat <<'EOF'
Usage: ./deploy_app1_colmap.sh [--base] [--no-restart] [--help]

Options:
  --base        Force a rebuild from scratch. Rebuild the COLMAP base image
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
# This includes COLMAP, Ceres, CuPy, and the native DroneGS trainer.
# Dependencies must be cloned first by setup_deps.sh.
if [[ "$BUILD_BASE" -eq 1 ]] || ! sudo docker image inspect drone-colmap-base:latest &>/dev/null; then
    for dep in \
        app1-colmap/ceres-solver/.git \
        app1-colmap/colmap-local/.git \
        app1-colmap/colmap-deps/poselib.zip \
        app1-colmap/colmap-deps/onnxruntime.tgz \
        app1-colmap/colmap-deps/aliked-n16rot.onnx \
        app1-colmap/colmap-deps/aliked-lightglue.onnx; do
        if [ ! -e "$dep" ]; then
            echo "❌ Missing dependency: $dep" >&2
            echo "   Run 'bash setup_deps.sh' first to clone external C++ dependencies." >&2
            exit 1
        fi
    done
    echo "🛠️ Building base image (COLMAP + DroneGS + Python deps)... This is slow but only needed once."
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
echo "🧩 Syncing Helm chart..."
DEPLOY_NS="drone-ai"
sudo helm upgrade --install drone-ai charts/drone-ai/ --namespace "$DEPLOY_NS" --create-namespace
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
    echo "🚀 Restarting colmap-worker deployment..."
    sudo kubectl rollout restart deployment colmap-worker -n "$DEPLOY_NS"
    sudo kubectl rollout status deployment colmap-worker -n "$DEPLOY_NS" --timeout=10m
    echo "✅ App 1 (COLMAP) deployed!"
else
    echo "✅ App 1 image staged in k3s; deployment not restarted."
fi
