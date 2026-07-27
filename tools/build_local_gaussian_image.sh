#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

docker build \
  --network=host \
  --progress=plain \
  --tag droneai-gaussian-local:latest \
  --file app1-colmap/Dockerfile.local-gaussian \
  .
