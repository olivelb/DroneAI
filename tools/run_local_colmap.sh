#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 DATASET WORKSPACE [run_local_colmap.py options...]" >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
dataset="$(realpath "$1")"
workspace="$2"
shift 2

if [[ ! -d "$dataset" ]]; then
  echo "Dataset directory does not exist: $dataset" >&2
  exit 2
fi

mkdir -p "$workspace"
workspace="$(realpath "$workspace")"
image="${DRONEAI_COLMAP_IMAGE:-drone-colmap:latest}"
preflight_image="${DRONEAI_PREFLIGHT_IMAGE:-droneai-api:local}"
gpu_args=(--gpus all)
gps_quality="standard"
preflight_filter_args=()
projection_args=()
arguments=("$@")
for ((index = 0; index < ${#arguments[@]}; index++)); do
  argument="${arguments[$index]}"
  if [[ "$argument" == "--no-use-gpu" ]]; then
    gpu_args=()
  elif [[ "$argument" == "--gps-quality" && $((index + 1)) -lt ${#arguments[@]} ]]; then
    gps_quality="${arguments[$((index + 1))]}"
  elif [[ "$argument" == --gps-quality=* ]]; then
    gps_quality="${argument#*=}"
  elif [[ "$argument" == "--include-prefix" && $((index + 1)) -lt ${#arguments[@]} ]]; then
    preflight_filter_args+=(--include-prefix "${arguments[$((index + 1))]}")
  elif [[ "$argument" == --include-prefix=* ]]; then
    preflight_filter_args+=(--include-prefix "${argument#*=}")
  elif [[ "$argument" == "--projected-crs-mode" && $((index + 1)) -lt ${#arguments[@]} ]]; then
    projection_args+=(--projected-crs-mode "${arguments[$((index + 1))]}")
  elif [[ "$argument" == --projected-crs-mode=* ]]; then
    projection_args+=(--projected-crs-mode "${argument#*=}")
  elif [[ "$argument" == "--projected-crs" && $((index + 1)) -lt ${#arguments[@]} ]]; then
    projection_args+=(--projected-crs "${arguments[$((index + 1))]}")
  elif [[ "$argument" == --projected-crs=* ]]; then
    projection_args+=(--projected-crs "${argument#*=}")
  fi
done

if ! docker image inspect "$preflight_image" >/dev/null 2>&1; then
  echo "Preflight image not found: $preflight_image" >&2
  echo "Build it with: docker build -f app4-dashboard/api/Dockerfile -t $preflight_image ." >&2
  exit 2
fi

docker run --rm \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --volume "$repo_root:/repo:ro" \
  --volume "$dataset:/input:ro" \
  --volume "$workspace:/workspace" \
  "$preflight_image" \
  python3 /repo/tools/dataset_preflight.py /input \
  --gps-quality "$gps_quality" \
  --output /workspace/dataset_preflight.json \
  --geojson /workspace/flight_path.geojson \
  "${projection_args[@]}" \
  "${preflight_filter_args[@]}"

docker run --rm \
  "${gpu_args[@]}" \
  --user "$(id -u):$(id -g)" \
  --env HOME=/tmp \
  --volume "$repo_root:/repo:ro" \
  --volume "$dataset:/input:ro" \
  --volume "$workspace:/workspace" \
  "$image" \
  python3 /repo/tools/run_local_colmap.py /input /workspace "$@"
