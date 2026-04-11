#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
	cat <<'EOF'
Usage: ./deploy_app4_api.sh [--base] [--no-restart] [--help]

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

echo "🛠️ Building Drone Dashboard API..."
export DOCKER_BUILDKIT=1
sudo docker build "${DOCKER_BUILD_FLAGS[@]}" -t drone-dashboard-api:latest -f app4-dashboard/api/Dockerfile .
echo "🧹 Cleaning Docker build cache..."
sudo docker builder prune -f --filter 'until=1h' 2>/dev/null || true
sudo docker image prune -f 2>/dev/null || true
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-api:latest | sudo k3s ctr images import -
echo "🧩 Applying Kubernetes manifests to keep dashboard-api config and RBAC in sync..."
sudo kubectl apply -f kafka-local.yaml
sudo kubectl apply -f dashboard-api-rbac.yaml
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
	echo "🚀 Restarting dashboard-api deployment..."
	sudo kubectl rollout restart deployment dashboard-api -n kafka
	sudo kubectl rollout status deployment dashboard-api -n kafka --timeout=10m
	echo "✅ App 4 (Dashboard API) deployed!"
else
	echo "✅ App 4 API image staged in k3s; deployment not restarted."
fi
