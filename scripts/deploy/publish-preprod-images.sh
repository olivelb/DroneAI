#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: publish-preprod-images.sh REGISTRY/PROJECT GIT_SHA

Build and push the CPU service images from a clean checkout with its full Git
commit tag. Tags are labels, not immutable deployment references. The registry
project must already exist and `docker login REGISTRY` must have succeeded.

GPU images are excluded by default. Set INCLUDE_GPU_IMAGES=1 only for an
explicit GPU deployment. The local drone-colmap-base:GIT_SHA is then
reused; a missing revision-specific base is an error. Set both INCLUDE_GPU_IMAGES=1 and
REBUILD_COLMAP_BASE=1 to explicitly authorize the long CUDA/COLMAP base build.
EOF
}

[[ $# -eq 2 ]] || { usage >&2; exit 2; }

readonly REGISTRY_PROJECT="${1%/}"
readonly IMAGE_TAG="$2"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly REPO_ROOT

[[ "$REGISTRY_PROJECT" == */* ]] \
    || { printf 'REGISTRY/PROJECT must include the private Harbor project.\n' >&2; exit 2; }
[[ "$IMAGE_TAG" =~ ^[0-9a-f]{40}$ ]] \
    || { printf 'GIT_SHA must be a full 40-character lower-case hexadecimal commit.\n' >&2; exit 2; }
cd "$REPO_ROOT"
[[ "$(git rev-parse HEAD)" == "$IMAGE_TAG" ]] \
    || { printf 'GIT_SHA must equal the checked-out HEAD.\n' >&2; exit 2; }
[[ -z "$(git status --porcelain --untracked-files=all)" ]] \
    || { printf 'Publication requires a clean tracked and untracked worktree.\n' >&2; exit 2; }

command -v docker >/dev/null 2>&1 || { printf 'docker is required.\n' >&2; exit 1; }
docker info >/dev/null 2>&1 || { printf 'The Docker daemon is unavailable.\n' >&2; exit 1; }

cd "$REPO_ROOT"
export DOCKER_BUILDKIT=1

readonly INCLUDE_GPU_IMAGES="${INCLUDE_GPU_IMAGES:-0}"
readonly REBUILD_COLMAP_BASE="${REBUILD_COLMAP_BASE:-0}"
readonly COLMAP_BASE_IMAGE="drone-colmap-base:$IMAGE_TAG"

if [[ "$REBUILD_COLMAP_BASE" == "1" && "$INCLUDE_GPU_IMAGES" != "1" ]]; then
    printf 'REBUILD_COLMAP_BASE=1 requires INCLUDE_GPU_IMAGES=1.\n' >&2
    exit 2
fi

if [[ "$INCLUDE_GPU_IMAGES" == "1" ]]; then
    if [[ "$REBUILD_COLMAP_BASE" == "1" ]]; then
        printf 'Explicitly building the CUDA/COLMAP base image (long operation)...\n'
        docker build \
            --network=host \
            --progress=plain \
            --label "org.opencontainers.image.revision=$IMAGE_TAG" \
            --tag "$COLMAP_BASE_IMAGE" \
            --file app1-colmap/Dockerfile.base \
            .
    elif [[ "$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$COLMAP_BASE_IMAGE" 2>/dev/null)" == "$IMAGE_TAG" ]]; then
        printf 'Reusing local %s.\n' "$COLMAP_BASE_IMAGE"
    else
        printf '%s\n' \
            "GPU publication refused: $COLMAP_BASE_IMAGE is missing or has a mismatched revision label." \
            'Set REBUILD_COLMAP_BASE=1 only when the long CUDA/COLMAP build is explicitly required.' >&2
        exit 1
    fi
fi

declare -A DOCKERFILES=(
    [drone-colmap]=app1-colmap/Dockerfile
    [drone-ia]=app2-ia/Dockerfile
    [drone-dashboard-api]=app4-dashboard/api/Dockerfile
    [drone-dashboard-frontend]=app4-dashboard/frontend/Dockerfile
)

images=(
    drone-dashboard-api
    drone-dashboard-frontend
)

if [[ "$INCLUDE_GPU_IMAGES" == "1" ]]; then
    images=(drone-colmap drone-ia "${images[@]}")
fi

for image in "${images[@]}"; do
    reference="$REGISTRY_PROJECT/$image:$IMAGE_TAG"
    build_args=()
    if [[ "$image" == "drone-colmap" ]]; then
        build_args+=(--build-arg "COLMAP_BASE_IMAGE=$COLMAP_BASE_IMAGE")
    fi
    docker build "${build_args[@]}" \
        --network=host \
        --progress=plain \
        --label "org.opencontainers.image.revision=$IMAGE_TAG" \
        --tag "$reference" \
        --file "${DOCKERFILES[$image]}" \
        .
    docker push "$reference"
    # Record the pushed content references for the deployment overlay.
    docker image inspect --format '{{json .RepoDigests}}' "$reference"
done

printf 'Published %s service images for commit %s; deploy the recorded OCI digests.\n' "${#images[@]}" "$IMAGE_TAG"
