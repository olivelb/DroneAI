#!/usr/bin/env bash

readonly SERVICE_IMAGES=(
    drone-colmap
    drone-ia
    drone-processing
    drone-dashboard-api
    drone-dashboard-frontend
)

SUDO=()
DOCKER=()

info() {
    printf '\033[1;34m==>\033[0m %s\n' "$*"
}

success() {
    printf '\033[1;32mOK:\033[0m %s\n' "$*"
}

warn() {
    printf '\033[1;33mWARN:\033[0m %s\n' "$*" >&2
}

fatal() {
    printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2
    exit 1
}

deployment_failed() {
    local line="$1"
    printf '\n\033[1;31mDeployment failed near line %s.\033[0m\n' "$line" >&2
    if [[ "${MODE:-}" == "local" ]] && ((${#DOCKER[@]})); then
        "${DOCKER[@]}" compose \
            --project-name droneai-local \
            --file "$REPO_ROOT/compose.local.yaml" \
            ps 2>/dev/null || true
    elif [[ "${MODE:-}" == "distributed" ]] && command -v k3s >/dev/null 2>&1; then
        "${SUDO[@]}" k3s kubectl get pods -n drone-ai 2>/dev/null || true
    fi
}

init_privilege() {
    if ((EUID != 0)); then
        command -v sudo >/dev/null 2>&1 \
            || fatal "sudo is required for first-run host and runtime setup."
        SUDO=(sudo)
    fi
}

set_docker_command() {
    if docker info >/dev/null 2>&1; then
        DOCKER=(docker)
    elif "${SUDO[@]}" docker info >/dev/null 2>&1; then
        DOCKER=(
            "${SUDO[@]}"
            "--preserve-env=DOCKER_BUILDKIT,HF_TOKEN,DRONEAI_DASHBOARD_PORT,DRONEAI_API_PORT,DRONEAI_MINIO_CONSOLE_PORT,DRONEAI_MINIO_API_PORT,DRONEAI_ACCESS_HOST"
            docker
        )
    else
        fatal "Docker is installed but the daemon is unavailable."
    fi
}

validate_host() {
    [[ "$(uname -s)" == "Linux" ]] \
        || fatal "DroneAI deployment supports Ubuntu and Ubuntu under WSL2."
    [[ -r /etc/os-release ]] || fatal "Unable to identify the Linux distribution."
    # shellcheck disable=SC1091
    source /etc/os-release
    case "${ID:-}" in
        ubuntu|debian) ;;
        *) fatal "Unsupported distribution '${ID:-unknown}'. Use Ubuntu or WSL Ubuntu." ;;
    esac
}

install_apt_packages() {
    local missing=()
    local package
    for package in "$@"; do
        dpkg-query --show --showformat='${Status}' "$package" 2>/dev/null \
            | grep -q "install ok installed" || missing+=("$package")
    done
    ((${#missing[@]})) || return 0
    info "Installing host packages: ${missing[*]}"
    "${SUDO[@]}" apt-get update
    "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive \
        apt-get install --yes "${missing[@]}"
}

install_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        info "Installing Docker Engine"
        local installer
        installer="$(mktemp)"
        curl --fail --silent --show-error --location https://get.docker.com \
            --output "$installer"
        "${SUDO[@]}" sh "$installer"
        rm -f -- "$installer"
    fi

    if ! docker info >/dev/null 2>&1; then
        "${SUDO[@]}" systemctl start docker 2>/dev/null \
            || "${SUDO[@]}" service docker start 2>/dev/null \
            || true
    fi

    set_docker_command

    if ! "${DOCKER[@]}" compose version >/dev/null 2>&1; then
        install_apt_packages docker-compose-plugin
    fi
    "${DOCKER[@]}" compose version >/dev/null 2>&1 \
        || fatal "Docker Compose v2 is required."
}

install_nvidia_toolkit() {
    if ! command -v nvidia-ctk >/dev/null 2>&1; then
        info "Installing NVIDIA Container Toolkit"
        curl --fail --silent --show-error --location \
            https://nvidia.github.io/libnvidia-container/gpgkey \
            | "${SUDO[@]}" gpg --dearmor --yes \
                --output /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl --fail --silent --show-error --location \
            https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
            | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
            | "${SUDO[@]}" tee /etc/apt/sources.list.d/nvidia-container-toolkit.list \
                >/dev/null
        "${SUDO[@]}" apt-get update
        "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive \
            apt-get install --yes nvidia-container-toolkit
    fi

    "${SUDO[@]}" nvidia-ctk runtime configure --runtime=docker >/dev/null
    "${SUDO[@]}" systemctl restart docker 2>/dev/null \
        || "${SUDO[@]}" service docker restart 2>/dev/null \
        || fatal "Unable to restart Docker after NVIDIA runtime configuration."

    set_docker_command
}

prepare_host() {
    local mode="$1"
    if ! "$SETUP_HOST"; then
        command -v curl >/dev/null 2>&1 || fatal "curl is missing."
        command -v git >/dev/null 2>&1 || fatal "git is missing."
        command -v docker >/dev/null 2>&1 || fatal "Docker is missing."
        init_docker_command
        return
    fi

    install_apt_packages \
        ca-certificates curl git gnupg jq openssl unzip xz-utils
    install_docker
    if [[ "$mode" == "distributed" ]]; then
        [[ "$(ps --pid 1 --format comm= | xargs)" == "systemd" ]] \
            || fatal "Distributed mode requires systemd. Under WSL, enable systemd in /etc/wsl.conf and restart WSL."
        install_nvidia_toolkit
        install_apt_packages conntrack iptables
    elif ! docker_gpu_smoke; then
        install_nvidia_toolkit
    fi
}

init_docker_command() {
    set_docker_command
    "${DOCKER[@]}" compose version >/dev/null 2>&1 \
        || fatal "Docker Compose v2 is required."
}

host_nvidia_smi() {
    if command -v nvidia-smi >/dev/null 2>&1; then
        command nvidia-smi "$@"
    elif [[ -x /usr/lib/wsl/lib/nvidia-smi ]]; then
        /usr/lib/wsl/lib/nvidia-smi "$@"
    else
        return 127
    fi
}

docker_gpu_smoke() {
    "${DOCKER[@]}" run --rm --gpus all \
        nvidia/cuda:12.8.1-base-ubuntu24.04 \
        nvidia-smi --query-gpu=name --format=csv,noheader >/dev/null 2>&1
}

validate_gpu() {
    host_nvidia_smi >/dev/null 2>&1 \
        || fatal "NVIDIA GPU unavailable. Install a compatible host driver first."
    local gpu_name
    gpu_name="$(host_nvidia_smi --query-gpu=name --format=csv,noheader | head -n 1)"
    info "GPU detected: $gpu_name"

    info "Validating GPU access from Docker"
    docker_gpu_smoke
    success "Docker can access the NVIDIA GPU"
}

validate_capacity() {
    local memory_gib free_gib
    memory_gib="$(awk '/MemTotal/ {printf "%d", $2 / 1024 / 1024}' /proc/meminfo)"
    free_gib="$(df --output=avail -BG "$REPO_ROOT" | tail -n 1 | tr -dc '0-9')"
    ((memory_gib >= 16)) \
        || fatal "At least 16 GiB RAM is required; detected ${memory_gib} GiB."
    if ((free_gib < 60)); then
        warn "Only ${free_gib} GiB free. A clean CUDA/COLMAP build may require 60–100 GiB."
    fi
}

prepare_build_dependencies() {
    info "Preparing pinned COLMAP/Ceres build dependencies"
    bash "$REPO_ROOT/setup_deps.sh"
}

docker_build() {
    local image="$1"
    local dockerfile="$2"
    local flags=(--network=host --progress=plain --tag "$image:latest" --file "$dockerfile")
    if "$REBUILD_BASE"; then
        flags+=(--no-cache)
    fi
    "${DOCKER[@]}" build "${flags[@]}" "$REPO_ROOT"
}

build_all_images() {
    export DOCKER_BUILDKIT=1
    if "$REBUILD_BASE" \
        || ! "${DOCKER[@]}" image inspect drone-colmap-base:latest >/dev/null 2>&1; then
        info "Building COLMAP/CUDA base image"
        docker_build drone-colmap-base app1-colmap/Dockerfile.base
    else
        info "Reusing drone-colmap-base:latest"
    fi

    info "Building DroneAI service images"
    docker_build drone-colmap app1-colmap/Dockerfile
    docker_build drone-ia app2-ia/Dockerfile
    docker_build drone-processing app3-processing/Dockerfile
    docker_build drone-dashboard-api app4-dashboard/api/Dockerfile
    docker_build drone-dashboard-frontend app4-dashboard/frontend/Dockerfile
    validate_service_images
}

validate_service_images() {
    local image
    for image in "${SERVICE_IMAGES[@]}"; do
        "${DOCKER[@]}" image inspect "$image:latest" >/dev/null 2>&1 \
            || fatal "Missing image $image:latest. Remove --no-build or build it first."
    done
}

wait_for_http() {
    local url="$1"
    local label="$2"
    local attempts="${3:-90}"
    local index
    for ((index = 1; index <= attempts; index++)); do
        if curl --fail --silent --show-error --max-time 3 "$url" >/dev/null 2>&1; then
            success "$label is ready"
            return 0
        fi
        sleep 2
    done
    fatal "$label did not become ready at $url."
}

is_wsl() {
    grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null
}

detect_distributed_access_host() {
    if is_wsl; then
        hostname -I | awk '{print $1}'
    else
        printf 'localhost\n'
    fi
}
