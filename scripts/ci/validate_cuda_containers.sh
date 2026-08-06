#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly REPOSITORY_ROOT
readonly RUN_KEY="${GITHUB_RUN_ID:-local}-$$"
readonly DEV_IMAGE="droneai-dronegs-dev-ci:${RUN_KEY}"
readonly BASE_BUILDER_IMAGE="droneai-base-builder-ci:${RUN_KEY}"
readonly LOCAL_BUILDER_IMAGE="droneai-local-builder-ci:${RUN_KEY}"

cleanup() {
    docker image rm --force \
        "${DEV_IMAGE}" \
        "${BASE_BUILDER_IMAGE}" \
        "${LOCAL_BUILDER_IMAGE}" \
        >/dev/null 2>&1 || true
}

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command not found: $1" >&2
        exit 1
    fi
}

report_validation_context() {
    echo "DroneAI commit: $(git -C "${REPOSITORY_ROOT}" rev-parse HEAD)"
    echo "Docker server: $(docker version --format '{{.Server.Version}}')"
    echo "CUDA image contracts:"
    awk '$1 == "FROM" && $2 ~ /^nvidia\/cuda:/ { print "  " $2 }' \
        "${REPOSITORY_ROOT}/app1-colmap/dronegs/Dockerfile" \
        "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.base" \
        "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.local-gaussian" \
        | sort -u
}

build_development_image() {
    docker build \
        --file "${REPOSITORY_ROOT}/app1-colmap/dronegs/Dockerfile" \
        --tag "${DEV_IMAGE}" \
        "${REPOSITORY_ROOT}/app1-colmap/dronegs"
}

compile_portable_in_development_image() {
    docker run --rm \
        --mount "type=bind,src=${REPOSITORY_ROOT},dst=/repo,readonly" \
        "${DEV_IMAGE}" \
        bash -euo pipefail -c '
            cmake -S /repo/app1-colmap/dronegs -B /tmp/dronegs-portable -G Ninja \
                -DCMAKE_BUILD_TYPE=Release \
                -DDRONEGS_CUDA_ARCHITECTURES=portable \
                -DDRONEGS_BUILD_TESTS=OFF \
                -DDRONEGS_BUILD_BENCHMARKS=OFF
            cmake --build /tmp/dronegs-portable --target dronegs
            /tmp/dronegs-portable/dronegs --version
        '
}

build_production_builder_stages() {
    docker build \
        --file "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.base" \
        --target dronegs-builder \
        --tag "${BASE_BUILDER_IMAGE}" \
        "${REPOSITORY_ROOT}"
    docker run --rm \
        "${BASE_BUILDER_IMAGE}" \
        /build/dronegs/build/dronegs --version

    docker build \
        --file "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.local-gaussian" \
        --target dronegs-builder \
        --tag "${LOCAL_BUILDER_IMAGE}" \
        "${REPOSITORY_ROOT}"
    docker run --rm \
        "${LOCAL_BUILDER_IMAGE}" \
        /build/dronegs/build/dronegs --version
}

run_native_gpu_tests_in_development_image() {
    docker run --rm --gpus all \
        --mount "type=bind,src=${REPOSITORY_ROOT},dst=/repo,readonly" \
        "${DEV_IMAGE}" \
        bash -euo pipefail -c '
            nvidia-smi --query-gpu=name,driver_version,compute_cap \
                --format=csv,noheader
            cmake -S /repo/app1-colmap/dronegs -B /tmp/dronegs-gpu -G Ninja \
                -DCMAKE_BUILD_TYPE=Release \
                -DDRONEGS_CUDA_ARCHITECTURES=native \
                -DDRONEGS_BUILD_TESTS=ON \
                -DDRONEGS_BUILD_BENCHMARKS=OFF
            cmake --build /tmp/dronegs-gpu
            ctest --test-dir /tmp/dronegs-gpu --output-on-failure
        '
}

runtime_images() {
    awk '
        $1 == "FROM" && $2 ~ /^nvidia\/cuda:.*runtime/ { print $2 }
    ' \
        "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.base" \
        "${REPOSITORY_ROOT}/app1-colmap/Dockerfile.local-gaussian" \
        | sort -u
}

smoke_runtime_images_on_gpu() {
    local runtime_image

    while IFS= read -r runtime_image; do
        if [[ -z "${runtime_image}" ]]; then
            continue
        fi
        echo "Validating GPU driver injection in ${runtime_image}"
        docker run --rm --gpus all \
            "${runtime_image}" \
            nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
    done < <(runtime_images)
}

main() {
    local mode="${1:-}"

    require_command docker
    require_command git
    trap cleanup EXIT
    report_validation_context

    case "${mode}" in
        build)
            build_development_image
            compile_portable_in_development_image
            build_production_builder_stages
            ;;
        gpu)
            build_development_image
            run_native_gpu_tests_in_development_image
            smoke_runtime_images_on_gpu
            ;;
        *)
            echo "Usage: $0 {build|gpu}" >&2
            exit 2
            ;;
    esac
}

main "$@"
