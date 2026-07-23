#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 WORKSPACE [run_local_detection.py options...]" >&2
  exit 2
fi

workspace="$(realpath "$1")"
shift
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${DRONEAI_IA_IMAGE:-drone-ia:latest}"
model_cache="${DRONEAI_MODEL_CACHE:-$HOME/.cache/droneai/models}"

if [[ ! -f "$workspace/.droneai-local-workspace.json" ]]; then
  echo "Refusing unmarked workspace: $workspace" >&2
  exit 2
fi

mkdir -p "$model_cache"

docker run --rm \
  --gpus all \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --env AERIAL_MODEL_DIR=/models \
  --env YOLO_CONFIG_DIR=/tmp \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --volume "$repo_root:/repo:ro" \
  --volume "$workspace:/workspace" \
  --volume "$model_cache:/models" \
  "$image" \
  python3 /repo/tools/run_local_detection.py /workspace "$@"
