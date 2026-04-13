#!/bin/bash
# Script de build et déploiement du pipeline DroneAI (microservices)

set -euo pipefail

BUILD_BASE=0
NO_CACHE=0

usage() {
    cat <<'EOF'
Usage: ./build_and_deploy.sh [--base] [--help]

Options:
  --base    Force a full rebuild from scratch: rebuild the COLMAP base image and
            rebuild all Docker images with --no-cache before re-importing them
            into k3s and restarting the deployments.
  --help    Show this help message.
EOF
}

for arg in "$@"; do
    case "$arg" in
        --base)
            BUILD_BASE=1
            NO_CACHE=1
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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export DOCKER_BUILDKIT=1

DOCKER_BUILD_FLAGS=()
if [[ "$NO_CACHE" -eq 1 ]]; then
    DOCKER_BUILD_FLAGS+=(--no-cache)
fi

echo "� Checking external build dependencies..."
for dep in LichtFeld-Studio/.git .docker-vcpkg/.git app1-colmap/ceres-solver/.git app1-colmap/colmap-local/.git app1-colmap/colmap-deps/poselib.zip; do
    if [ ! -e "$dep" ]; then
        echo "❌ Missing dependency: $dep" >&2
        echo "   Run 'bash setup_deps.sh' first to clone external C++ dependencies." >&2
        exit 1
    fi
done
echo "✅ All external dependencies present."

echo "🔐 Checking required Kubernetes secrets..."
DEPLOY_NS="drone-ai"
# Check both old (kafka) and new (drone-ai) namespaces for the secret
if sudo kubectl get secret hf-token -n "$DEPLOY_NS" >/dev/null 2>&1; then
    :
elif sudo kubectl get secret hf-token -n kafka >/dev/null 2>&1; then
    echo "   ⚠️  Found hf-token in 'kafka' namespace but not in '$DEPLOY_NS'."
    echo "   Creating namespace '$DEPLOY_NS' if needed and copying secret..."
    sudo kubectl create namespace "$DEPLOY_NS" --dry-run=client -o yaml | sudo kubectl apply -f -
    sudo kubectl get secret hf-token -n kafka -o yaml \
        | sed "s/namespace: kafka/namespace: $DEPLOY_NS/" \
        | sudo kubectl apply -f -
else
    echo "❌ Missing secret 'hf-token'."
    echo "   Create it before deploying App 2, for example:"
    echo "   export HF_TOKEN=your_huggingface_token"
    echo "   sudo kubectl create namespace $DEPLOY_NS"
    echo "   sudo kubectl -n $DEPLOY_NS create secret generic hf-token --from-literal=HF_TOKEN=\"\$HF_TOKEN\""
    exit 1
fi

echo "🛠️ Construction des images microservices..."
if [[ "$BUILD_BASE" -eq 1 ]]; then
    echo "   -> Full rebuild requested: base image and all service images will be rebuilt with Docker cache disabled."
fi

# 0. Base image required by the COLMAP worker
if [[ "$BUILD_BASE" -eq 1 ]] || ! sudo docker image inspect drone-colmap-base:latest >/dev/null 2>&1; then
    echo "   -> Building Drone COLMAP base image..."
    sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap-base:latest -f app1-colmap/Dockerfile.base .
    echo "   -> Importing Drone COLMAP base image into k3s..."
    sudo docker save drone-colmap-base:latest > drone-colmap-base.tar
    sudo k3s ctr images import drone-colmap-base.tar
    rm drone-colmap-base.tar
else
    echo "   -> Reusing existing Drone COLMAP base image."
fi

# 1. COLMAP Worker
echo "   -> Building Drone COLMAP Worker..."
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" --progress=plain -t drone-colmap:latest -f app1-colmap/Dockerfile .

# 2. IA Worker
echo "   -> Building Drone IA Worker..."
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" -t drone-ia:latest -f app2-ia/Dockerfile .

# 3. Processing Worker
echo "   -> Building Drone Processing Worker..."
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" -t drone-processing:latest -f app3-processing/Dockerfile .

# 4. Dashboard API
echo "   -> Building Drone Dashboard API..."
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .

# 5. Dashboard Frontend
echo "   -> Building Drone Dashboard Frontend..."
sudo docker build --network=host "${DOCKER_BUILD_FLAGS[@]}" -t drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .

echo "📦 Importation des images dans k3s..."

IMAGES=("drone-colmap" "drone-ia" "drone-processing" "drone-dashboard-api" "drone-dashboard-frontend")

for IMG in "${IMAGES[@]}"; do
    echo "   -> Exporting/Importing $IMG..."
    sudo docker save "$IMG:latest" > "$IMG.tar"
    sudo k3s ctr images import "$IMG.tar"
    rm "$IMG.tar"
done

echo "🚀 Déploiement sur Kubernetes (Helm)..."

# Install Helm if not present
if ! command -v helm &>/dev/null; then
    echo "   -> Installing Helm..."
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# Deploy with Helm
sudo helm upgrade --install drone-ai charts/drone-ai/ \
    --namespace "$DEPLOY_NS" \
    --create-namespace \
    --wait --timeout 5m

echo "✅ Pipeline microservices déployé (namespace: $DEPLOY_NS) !"
