#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ ! -d LichtFeld-Studio/.git || ! -d .docker-vcpkg/.git ]]; then
  echo "Missing pinned LichtFeld or vcpkg source." >&2
  echo "Run setup_deps.sh, or clone only the two dependencies documented in LOCAL_PIPELINE.md." >&2
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

docker build \
  --network=host \
  --progress=plain \
  --tag droneai-gaussian-local:latest \
  --file app1-colmap/Dockerfile.local-gaussian \
  .
