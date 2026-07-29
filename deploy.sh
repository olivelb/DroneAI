#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_LIB="$REPO_ROOT/scripts/deploy"

usage() {
    cat <<'EOF'
Usage:
  ./deploy.sh local [options]
  ./deploy.sh distributed [options]

Modes:
  local         Docker Compose deployment with the complete dashboard,
                Kafka, MinIO, PostgreSQL and all pipeline workers.
  distributed   Single-node K3s deployment through Helm, including the
                NVIDIA device plugin and the complete dashboard.

Options:
  --base                    Rebuild the COLMAP base and every service without cache.
  --no-build                Reuse already-built Docker images.
  --skip-host-setup         Do not install missing host packages or runtimes.
  --data-root PATH          Persistent runtime root (distributed mode).
  --dashboard-port PORT     Dashboard port (default: 30000).
  --api-port PORT           Dashboard API port (default: 30080).
  --minio-console-port PORT MinIO console port (default: 30090).
  --minio-api-port PORT     Browser-facing MinIO API port (default: 30091).
  --help                    Show this help.

Environment:
  HF_TOKEN                  Optional. Required only for Hugging Face gated models.
  DRONEAI_DATA_ROOT         Alternative default for --data-root.

Examples:
  git clone https://github.com/olivelb/DroneAI.git
  cd DroneAI
  ./deploy.sh local

  ./deploy.sh distributed
  ./deploy.sh distributed --base
EOF
}

if [[ $# -lt 1 ]]; then
    usage >&2
    exit 2
fi

MODE="$1"
shift
case "$MODE" in
    local|distributed) ;;
    --help|-h)
        usage
        exit 0
        ;;
    *)
        printf 'Unsupported deployment mode: %s\n\n' "$MODE" >&2
        usage >&2
        exit 2
        ;;
esac

# These settings are consumed by the sourced deployment backend.
# shellcheck disable=SC2034
REBUILD_BASE=false
BUILD_IMAGES=true
# shellcheck disable=SC2034
SETUP_HOST=true
# shellcheck disable=SC2034
DATA_ROOT="${DRONEAI_DATA_ROOT:-$HOME/.local/share/droneai/$MODE}"
DASHBOARD_PORT=30000
API_PORT=30080
MINIO_CONSOLE_PORT=30090
MINIO_API_PORT=30091

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)
            REBUILD_BASE=true
            shift
            ;;
        --no-build)
            BUILD_IMAGES=false
            shift
            ;;
        --skip-host-setup)
            SETUP_HOST=false
            shift
            ;;
        --data-root)
            [[ $# -ge 2 ]] || { echo "Missing value for --data-root" >&2; exit 2; }
            DATA_ROOT="$2"
            shift 2
            ;;
        --dashboard-port)
            [[ $# -ge 2 ]] || { echo "Missing value for --dashboard-port" >&2; exit 2; }
            DASHBOARD_PORT="$2"
            shift 2
            ;;
        --api-port)
            [[ $# -ge 2 ]] || { echo "Missing value for --api-port" >&2; exit 2; }
            API_PORT="$2"
            shift 2
            ;;
        --minio-console-port)
            [[ $# -ge 2 ]] || { echo "Missing value for --minio-console-port" >&2; exit 2; }
            MINIO_CONSOLE_PORT="$2"
            shift 2
            ;;
        --minio-api-port)
            [[ $# -ge 2 ]] || { echo "Missing value for --minio-api-port" >&2; exit 2; }
            MINIO_API_PORT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

DATA_ROOT="$(realpath --canonicalize-missing "$DATA_ROOT")"

for port in "$DASHBOARD_PORT" "$API_PORT" "$MINIO_CONSOLE_PORT" "$MINIO_API_PORT"; do
    if [[ ! "$port" =~ ^[0-9]+$ ]] || ((port < 1024 || port > 65535)); then
        echo "Invalid TCP port: $port" >&2
        exit 2
    fi
done

if [[ "$MODE" == "distributed" ]]; then
    for port in "$DASHBOARD_PORT" "$API_PORT" "$MINIO_CONSOLE_PORT" "$MINIO_API_PORT"; do
        if ((port < 30000 || port > 32767)); then
            echo "Kubernetes NodePort must be between 30000 and 32767: $port" >&2
            exit 2
        fi
    done
fi

# shellcheck source=scripts/deploy/common.sh
source "$DEPLOY_LIB/common.sh"
if [[ "$MODE" == "local" ]]; then
    # shellcheck source=scripts/deploy/local.sh
    source "$DEPLOY_LIB/local.sh"
else
    # shellcheck source=scripts/deploy/distributed.sh
    source "$DEPLOY_LIB/distributed.sh"
fi

trap 'deployment_failed "$LINENO"' ERR

main() {
    cd "$REPO_ROOT"
    init_privilege
    validate_host
    prepare_host "$MODE"
    validate_gpu
    validate_capacity

    if "$BUILD_IMAGES"; then
        prepare_build_dependencies
        build_all_images
    else
        validate_service_images
    fi

    "deploy_${MODE}"
}

main
