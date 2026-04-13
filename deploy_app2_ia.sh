#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
	cat <<'EOF'
Usage: ./deploy_app2_ia.sh [--base] [--no-restart] [--help]

Options:
  --base        Force a rebuild from scratch by rebuilding the image with Docker cache disabled.
  --no-restart  Build and import the image into k3s without restarting the deployment.
  --help        Show this help message.
EOF
}

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

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

DOCKER_BUILD_FLAGS=()
if [[ "$BUILD_BASE" -eq 1 ]]; then
	DOCKER_BUILD_FLAGS+=(--no-cache)
fi

echo "🔐 Checking required Kubernetes secrets..."
DEPLOY_NS="drone-ai"
if sudo kubectl get secret hf-token -n "$DEPLOY_NS" >/dev/null 2>&1; then
	:
elif sudo kubectl get secret hf-token -n kafka >/dev/null 2>&1; then
	echo "   ⚠️  Found hf-token in 'kafka' namespace but not in '$DEPLOY_NS'. Copying..."
	sudo kubectl create namespace "$DEPLOY_NS" --dry-run=client -o yaml | sudo kubectl apply -f -
	sudo kubectl get secret hf-token -n kafka -o yaml \
		| sed "s/namespace: kafka/namespace: $DEPLOY_NS/" \
		| sudo kubectl apply -f -
else
	echo "❌ Missing secret 'hf-token' in namespace '$DEPLOY_NS'."
	echo "   Create it before deploying App 2, for example:"
	echo "   export HF_TOKEN=your_huggingface_token"
	echo "   sudo kubectl -n $DEPLOY_NS create secret generic hf-token --from-literal=HF_TOKEN=\"\$HF_TOKEN\""
	exit 1
fi

echo "🛠️ Building Drone IA Worker..."
export DOCKER_BUILDKIT=1
sudo docker build "${DOCKER_BUILD_FLAGS[@]}" -t drone-ia:latest -f app2-ia/Dockerfile .
echo "🧹 Cleaning Docker build cache..."
sudo docker builder prune -f --filter 'until=1h' 2>/dev/null || true
sudo docker image prune -f 2>/dev/null || true
echo "📦 Importing image to k3s..."
sudo docker save drone-ia:latest | sudo k3s ctr images import -
echo "🧩 Syncing Helm chart..."
sudo helm upgrade --install drone-ai charts/drone-ai/ --namespace "$DEPLOY_NS" --create-namespace
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
	echo "🚀 Restarting ia-worker deployment..."
	sudo kubectl rollout restart deployment ia-worker -n "$DEPLOY_NS"
	sudo kubectl rollout status deployment ia-worker -n "$DEPLOY_NS" --timeout=10m
	echo "✅ App 2 (IA) deployed!"
else
	echo "✅ App 2 image staged in k3s; deployment not restarted."
fi
