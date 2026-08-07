#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: publish-preprod-images.sh REGISTRY/PROJECT GIT_SHA

Build and push DroneAI service images with one immutable Git commit tag.
The registry project must already exist and `docker login REGISTRY` must have
succeeded. A missing local drone-colmap-base:latest triggers the long CUDA and
COLMAP base build; set REBUILD_COLMAP_BASE=1 to rebuild it explicitly.
EOF
}

[[ $# -eq 2 ]] || { usage >&2; exit 2; }

readonly REGISTRY_PROJECT="${1%/}"
readonly IMAGE_TAG="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly REPO_ROOT

[[ "$REGISTRY_PROJECT" == */* ]] \
    || { printf 'REGISTRY/PROJECT must include the private Harbor project.\n' >&2; exit 2; }
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{7,40}$ ]] \
    || { printf 'GIT_SHA must be a 7-40 character lower-case hexadecimal commit.\n' >&2; exit 2; }
command -v docker >/dev/null 2>&1 || { printf 'docker is required.\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf 'The Docker daemon is unavailable.\n' >&2; exit 1; }

cd "$REPO_ROOT"
export DOCKER_BUILDKIT=1

if [[ "${REBUILD_COLMAP_BASE:-0}" == "1" ]] \
    || ! docker image inspect drone-colmap-base:latest >/dev/null 2>&1; then
    printf 'Building the CUDA/COLMAP base image (long operation)...\n'
    docker build \
        --network=host \
        --progress=plain \
        --tag drone-colmap-base:latest \
        --file app1-colmap/Dockerfile.base \
        .
else
    printf 'Reusing local drone-colmap-base:latest.\n'
fi

declare -A DOCKERFILES=(
    [drone-colmap]=app1-colmap/Dockerfile
    [drone-ia]=app2-ia/Dockerfile
    [drone-processing]=app3-processing/Dockerfile
    [drone-dashboard-api]=app4-dashboard/api/Dockerfile
    [drone-dashboard-frontend]=app4-dashboard/frontend/Dockerfile
)

for image in \
    drone-colmap \
    drone-ia \
    drone-processing \
    drone-dashboard-api \
    drone-dashboard-frontend
do
    reference="$REGISTRY_PROJECT/$image:$IMAGE_TAG"
    docker build \
        --network=host \
        --progress=plain \
        --tag "$reference" \
        --file "${DOCKERFILES[$image]}" \
        .
    docker push "$reference"
done

printf 'Published all service images with immutable tag %s.\n' "$IMAGE_TAG"
