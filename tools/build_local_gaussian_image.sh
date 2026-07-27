#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

with_lichtfeld=0
if [[ "${1:-}" == "--with-lichtfeld" ]]; then
  with_lichtfeld=1
  shift
fi
if [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--with-lichtfeld]" >&2
  exit 2
fi

runtime_args=()
if [[ "$with_lichtfeld" -eq 1 ]]; then
  if [[ ! -d LichtFeld-Studio/.git || ! -d .docker-vcpkg/.git ]]; then
    echo "Missing pinned LichtFeld or vcpkg source." >&2
    echo "Run setup_deps.sh --with-lichtfeld first." >&2
    exit 2
  fi

  if [[ "$(git -C .docker-vcpkg rev-parse --is-shallow-repository)" == "true" ]]; then
    echo "Completing the vcpkg history required by manifest versioning..."
    git -C .docker-vcpkg fetch --unshallow
  fi

  docker build \
    --network=host \
    --progress=plain \
    --tag lichtfeld-runtime:latest \
    --file app1-colmap/Dockerfile.lichtfeld \
    .
  runtime_args=(
    --build-arg
    GAUSSIAN_RUNTIME_IMAGE=lichtfeld-runtime:latest
  )
fi

docker build \
  --network=host \
  --progress=plain \
  "${runtime_args[@]}" \
  --tag droneai-gaussian-local:latest \
  --file app1-colmap/Dockerfile.local-gaussian \
  .
