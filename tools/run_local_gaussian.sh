#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 WORKSPACE [run_local_gaussian.py options...]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$1"
shift

if [[ ! -d "$workspace" ]]; then
  echo "Workspace directory does not exist: $workspace" >&2
  exit 2
fi

workspace="$(realpath "$workspace")"
image="${DRONEAI_GAUSSIAN_IMAGE:-droneai-gaussian-local:latest}"

if ! docker image inspect "$image" >/dev/null 2>&1; then
  echo "Local Gaussian image not found: $image" >&2
  echo "Build it with: tools/build_local_gaussian_image.sh" >&2
  exit 2
fi

docker run --rm \
  --gpus all \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --volume "$repo_root:/repo:ro" \
  --volume "$workspace:/workspace" \
  "$image" \
  python3 /repo/tools/run_local_gaussian.py /workspace "$@"
