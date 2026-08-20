#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 WORKSPACE [run_local_gaussian.py options...]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
workspace="$1"
shift

container_arguments=()
checkpoint_root_host=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --checkpoint-root)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "--checkpoint-root requires a host path" >&2
        exit 2
      fi
      if [[ -n "$checkpoint_root_host" ]]; then
        echo "--checkpoint-root may be specified only once" >&2
        exit 2
      fi
      checkpoint_root_host="$2"
      shift 2
      ;;
    --checkpoint-root=*)
      if [[ -n "$checkpoint_root_host" ]]; then
        echo "--checkpoint-root may be specified only once" >&2
        exit 2
      fi
      checkpoint_root_host="${1#*=}"
      if [[ -z "$checkpoint_root_host" ]]; then
        echo "--checkpoint-root requires a host path" >&2
        exit 2
      fi
      shift
      ;;
    *)
      container_arguments+=("$1")
      shift
      ;;
  esac
done

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

checkpoint_mount=()
if [[ -n "$checkpoint_root_host" ]]; then
  mkdir -p "$checkpoint_root_host"
  checkpoint_root_host="$(realpath "$checkpoint_root_host")"
  checkpoint_mount=(
    --volume "$checkpoint_root_host:/checkpoints"
  )
  container_arguments+=(--checkpoint-root /checkpoints)
fi

docker run --rm \
  --gpus all \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --volume "$repo_root:/repo:ro" \
  --volume "$workspace:/workspace" \
  "${checkpoint_mount[@]}" \
  "$image" \
  python3 /repo/tools/run_local_gaussian.py /workspace "${container_arguments[@]}"
