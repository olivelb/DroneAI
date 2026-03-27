#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
	cat <<'EOF'
Usage: ./deploy_app4_frontend.sh [--base] [--no-restart] [--help]

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

echo "🛠️ Building Drone Dashboard Frontend..."
export DOCKER_BUILDKIT=1
sudo docker build "${DOCKER_BUILD_FLAGS[@]}" -t drone-dashboard-frontend:latest -f app4-dashboard/frontend/Dockerfile .
echo "📦 Importing image to k3s..."
sudo docker save drone-dashboard-frontend:latest > drone-dashboard-frontend.tar
sudo k3s ctr images import drone-dashboard-frontend.tar
rm drone-dashboard-frontend.tar
echo "🧩 Applying Kubernetes manifest to keep dashboard-frontend config in sync..."
sudo kubectl apply -f kafka-local.yaml
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
	echo "🚀 Restarting dashboard-frontend deployment..."
	sudo kubectl rollout restart deployment dashboard-frontend -n kafka
	sudo kubectl rollout status deployment dashboard-frontend -n kafka --timeout=10m
	echo "✅ App 4 (Dashboard Frontend) deployed!"
else
	echo "✅ App 4 frontend image staged in k3s; deployment not restarted."
fi
