#!/bin/bash
set -euo pipefail

BUILD_BASE=0
RESTART_DEPLOYMENT=1

usage() {
	cat <<'EOF'
Usage: ./deploy_app3_processing.sh [--base] [--no-restart] [--help]

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

echo "🛠️ Building Drone Processing Worker..."
export DOCKER_BUILDKIT=1
sudo docker build "${DOCKER_BUILD_FLAGS[@]}" -t drone-processing:latest -f app3-processing/Dockerfile .
echo "🧹 Cleaning Docker build cache..."
sudo docker builder prune -f --filter 'until=1h' 2>/dev/null || true
sudo docker image prune -f 2>/dev/null || true
echo "📦 Importing image to k3s..."
sudo docker save drone-processing:latest | sudo k3s ctr images import -
echo "🧩 Applying Kubernetes manifest to keep processing-worker config in sync..."
sudo kubectl apply -f kafka-local.yaml
if [[ "$RESTART_DEPLOYMENT" -eq 1 ]]; then
	echo "🚀 Restarting processing-worker deployment..."
	sudo kubectl rollout restart deployment processing-worker -n kafka
	sudo kubectl rollout status deployment processing-worker -n kafka --timeout=10m
	echo "✅ App 3 (Processing) deployed!"
else
	echo "✅ App 3 image staged in k3s; deployment not restarted."
fi
