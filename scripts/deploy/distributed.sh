#!/usr/bin/env bash

kube() {
    "${SUDO[@]}" k3s kubectl "$@"
}

helm_root() {
    "${SUDO[@]}" env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm "$@"
}

install_k3s() {
    if ! command -v k3s >/dev/null 2>&1; then
        info "Installing K3s"
        curl --fail --silent --show-error --location https://get.k3s.io \
            | "${SUDO[@]}" env \
                INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" \
                sh -
    fi

    "${SUDO[@]}" systemctl restart k3s
    kube wait --for=condition=Ready node --all --timeout=180s
}

install_helm() {
    if command -v helm >/dev/null 2>&1; then
        return
    fi
    info "Installing Helm"
    local installer
    installer="$(mktemp)"
    curl --fail --silent --show-error --location \
        https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
        --output "$installer"
    chmod +x "$installer"
    "$installer"
    rm -f -- "$installer"
}

install_gpu_plugin() {
    kube get runtimeclass nvidia >/dev/null 2>&1 \
        || fatal "K3s did not detect the NVIDIA runtime. Check nvidia-container-runtime and restart K3s."

    local minimum_vram_mib advertised_vram_gb
    local vram_at_least_8gb=false
    local vram_at_least_12gb=false
    local vram_at_least_24gb=false
    minimum_vram_mib="$(nvidia-smi \
        --query-gpu=memory.total \
        --format=csv,noheader,nounits \
        | awk '
            NR == 1 {minimum = $1}
            $1 < minimum {minimum = $1}
            END {if (minimum != "") printf "%d", minimum}
        ')"
    [[ "$minimum_vram_mib" =~ ^[1-9][0-9]*$ ]] \
        || fatal "Unable to determine the minimum physical GPU VRAM."
    advertised_vram_gb="${DRONEAI_GPU_VRAM_CLASS_GB:-$((
        (minimum_vram_mib * 1048576 + 500000000) / 1000000000
    ))}"
    [[ "$advertised_vram_gb" =~ ^[1-9][0-9]*$ ]] \
        || fatal "DRONEAI_GPU_VRAM_CLASS_GB must be a positive integer."
    if ((advertised_vram_gb >= 8)); then
        vram_at_least_8gb=true
    fi
    if ((advertised_vram_gb >= 12)); then
        vram_at_least_12gb=true
    fi
    if ((advertised_vram_gb >= 24)); then
        vram_at_least_24gb=true
    fi

    info "Labelling GPU node capabilities (${advertised_vram_gb} GB VRAM class)"
    kube label node --all \
        nvidia.com/gpu.present=true \
        "droneai.io/gpu-vram-at-least-8gb=$vram_at_least_8gb" \
        "droneai.io/gpu-vram-at-least-12gb=$vram_at_least_12gb" \
        "droneai.io/gpu-vram-at-least-24gb=$vram_at_least_24gb" \
        --overwrite >/dev/null

    info "Installing the NVIDIA Kubernetes device plugin"
    helm_root repo add nvdp https://nvidia.github.io/k8s-device-plugin \
        --force-update >/dev/null
    helm_root repo update >/dev/null
    helm_root upgrade --install nvidia-device-plugin nvdp/nvidia-device-plugin \
        --version 0.19.3 \
        --namespace nvidia-device-plugin \
        --create-namespace \
        --values "$REPO_ROOT/nvdp-values.yaml" \
        --wait \
        --timeout 5m
    kube rollout status daemonset/nvidia-device-plugin \
        --namespace nvidia-device-plugin \
        --timeout=180s

    local index allocatable
    for ((index = 1; index <= 60; index++)); do
        allocatable="$(kube get nodes \
            --output=jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}' \
            2>/dev/null || true)"
        if [[ "$allocatable" =~ ^[1-9][0-9]*$ ]]; then
            success "Kubernetes exposes $allocatable GPU allocation slot(s)"
            return
        fi
        sleep 2
    done
    fatal "The NVIDIA device plugin started but no GPU is allocatable."
}

import_images_into_k3s() {
    info "Importing service images into K3s containerd"
    local image image_tag docker_id k3s_digest reference
    image_tag="$(application_image_tag)"
    while IFS= read -r image; do
        reference="docker.io/library/$image:$image_tag"
        docker_id="$("${DOCKER[@]}" image inspect \
            --format '{{.Id}}' "$image:$image_tag")"
        k3s_digest="$("${SUDO[@]}" k3s ctr images list 2>/dev/null \
            | awk -v reference="$reference" '
                $1 == reference {digest = $3}
                END {if (digest) print digest}
            ')"
        if [[ -n "$k3s_digest" && "$docker_id" == "$k3s_digest" ]]; then
            info "Reusing $image:$image_tag already imported in K3s"
            continue
        fi
        info "Importing $image:$image_tag"
        "${DOCKER[@]}" save "$image:$image_tag" \
            | "${SUDO[@]}" k3s ctr images import -
    done < <(active_service_images)
}

ensure_helm_namespace() {
    if ! kube get namespace drone-ai >/dev/null 2>&1; then
        kube create namespace drone-ai
    fi
    kube label namespace drone-ai app.kubernetes.io/managed-by=Helm \
        --overwrite >/dev/null
    kube annotate namespace drone-ai \
        meta.helm.sh/release-name=drone-ai \
        meta.helm.sh/release-namespace=drone-ai \
        --overwrite >/dev/null

    local hf_token="${HF_TOKEN:-}"
    if [[ -z "$hf_token" && -r "$HOME/.cache/huggingface/token" ]]; then
        hf_token="$(<"$HOME/.cache/huggingface/token")"
    fi
    kube create secret generic hf-token \
        --namespace drone-ai \
        --from-literal="HF_TOKEN=$hf_token" \
        --dry-run=client \
        --output=yaml \
        | kube apply --filename=- >/dev/null
}

node_port_owner() {
    local port="$1"
    kube get services --all-namespaces --output=json \
        | jq --argjson port "$port" -r '
            .items[]
            | select(any(.spec.ports[]?; .nodePort == $port))
            | "\(.metadata.namespace)/\(.metadata.name)"
        ' \
        | head -n 1
}

resolve_node_port() {
    local requested="$1"
    local expected_owner="$2"
    local candidate owner
    for ((candidate = requested; candidate <= 32767; candidate++)); do
        owner="$(node_port_owner "$candidate")"
        if [[ -z "$owner" || "$owner" == "$expected_owner" ]]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    fatal "No free Kubernetes NodePort at or above $requested."
}

distributed_memory_values() {
    local memory_gib colmap_limit processing_limit
    memory_gib="$(awk '/MemTotal/ {printf "%d", $2 / 1024 / 1024}' /proc/meminfo)"
    colmap_limit=$((memory_gib - 4))
    ((colmap_limit > 80)) && colmap_limit=80
    ((colmap_limit < 12)) && colmap_limit=12
    processing_limit=$((memory_gib / 3))
    ((processing_limit > 16)) && processing_limit=16
    ((processing_limit < 4)) && processing_limit=4
    printf '%s %s\n' "$colmap_limit" "$processing_limit"
}

deploy_distributed() {
    install_k3s
    install_helm
    install_gpu_plugin
    import_images_into_k3s
    ensure_helm_namespace

    mkdir -p \
        "$DATA_ROOT/kafka-data" \
        "$DATA_ROOT/minio-data" \
        "$DATA_ROOT/model-cache" \
        "$DATA_ROOT/colmap-work" \
        "$DATA_ROOT/postgres-data"
    chmod 0777 \
        "$DATA_ROOT/kafka-data" \
        "$DATA_ROOT/minio-data" \
        "$DATA_ROOT/model-cache" \
        "$DATA_ROOT/postgres-data"
    "${SUDO[@]}" chown 10001:10001 "$DATA_ROOT/colmap-work"
    "${SUDO[@]}" chmod 0770 "$DATA_ROOT/colmap-work"

    DASHBOARD_PORT="$(resolve_node_port "$DASHBOARD_PORT" drone-ai/dashboard-frontend-service)"
    API_PORT="$(resolve_node_port "$API_PORT" drone-ai/dashboard-api-service)"
    MINIO_CONSOLE_PORT="$(resolve_node_port "$MINIO_CONSOLE_PORT" drone-ai/minio-console)"
    MINIO_API_PORT="$(resolve_node_port "$MINIO_API_PORT" drone-ai/minio-api)"

    local access_host memory_values colmap_limit processing_limit drives_json
    access_host="$(detect_distributed_access_host)"
    memory_values="$(distributed_memory_values)"
    read -r colmap_limit processing_limit <<<"$memory_values"
    drives_json="$(discover_work_drives)"
    info "Work drives: $(jq --raw-output 'map(.label) | join(", ")' <<<"$drives_json")"

    local stage_job_values=()
    if [[ -n "${STAGE_JOBS_IMAGE_TAG:-}" ]]; then
        stage_job_values=(
            --set global.requireImmutableImages=true
            --set-string "colmapWorker.tag=$STAGE_JOBS_IMAGE_TAG"
            --set-string "iaWorker.tag=$STAGE_JOBS_IMAGE_TAG"
            --set-string "processingWorker.tag=$STAGE_JOBS_IMAGE_TAG"
            --set-string "dashboardApi.tag=$STAGE_JOBS_IMAGE_TAG"
            --set-string "dashboardFrontend.tag=$STAGE_JOBS_IMAGE_TAG"
            --set colmapWorker.enabled=false
            --set iaWorker.enabled=false
            --set processingWorker.replicaCount=0
            --set stageJobs.enabled=true
            --set-string "stageJobs.executors.reconstruction.image=drone-colmap:$STAGE_JOBS_IMAGE_TAG"
            --set-json 'stageJobs.executors.reconstruction.command=["python3","app1-colmap/stage_executor.py","reconstruction"]'
            --set-string stageJobs.executors.reconstruction.gpu_architecture=ampere
            --set-json 'stageJobs.executors.reconstruction.tolerations=[{"key":"nvidia.com/gpu","operator":"Equal","value":"present","effect":"NoSchedule"}]'
            --set-string "stageJobs.executors.gaussian_training.image=drone-colmap:$STAGE_JOBS_IMAGE_TAG"
            --set-json 'stageJobs.executors.gaussian_training.command=["python3","app1-colmap/stage_executor.py","gaussian_training"]'
            --set-string stageJobs.executors.gaussian_training.gpu_architecture=ampere
            --set-json 'stageJobs.executors.gaussian_training.tolerations=[{"key":"nvidia.com/gpu","operator":"Equal","value":"present","effect":"NoSchedule"}]'
            --set-string "stageJobs.executors.gaussian_filtering.image=drone-colmap:$STAGE_JOBS_IMAGE_TAG"
            --set-json 'stageJobs.executors.gaussian_filtering.command=["python3","app1-colmap/stage_executor.py","gaussian_filtering"]'
            --set-string stageJobs.executors.gaussian_filtering.gpu_architecture=ampere
            --set-json 'stageJobs.executors.gaussian_filtering.tolerations=[{"key":"nvidia.com/gpu","operator":"Equal","value":"present","effect":"NoSchedule"}]'
            --set-string "stageJobs.executors.rasterization.image=drone-colmap:$STAGE_JOBS_IMAGE_TAG"
            --set-json 'stageJobs.executors.rasterization.command=["python3","app1-colmap/stage_executor.py","rasterization"]'
            --set-string stageJobs.executors.rasterization.gpu_architecture=ampere
            --set-json 'stageJobs.executors.rasterization.tolerations=[{"key":"nvidia.com/gpu","operator":"Equal","value":"present","effect":"NoSchedule"}]'
            --set-string "stageJobs.executors.detection.image=drone-ia:$STAGE_JOBS_IMAGE_TAG"
            --set-json 'stageJobs.executors.detection.command=["python3","app2-ia/stage_executor.py"]'
            --set-string stageJobs.executors.detection.gpu_architecture=ampere
            --set-json 'stageJobs.executors.detection.tolerations=[{"key":"nvidia.com/gpu","operator":"Equal","value":"present","effect":"NoSchedule"}]'
        )
    fi

    info "Deploying DroneAI through Helm"
    helm_root upgrade --install drone-ai "$REPO_ROOT/charts/drone-ai" \
        --namespace drone-ai \
        --set-string "kafka.persistence.hostPath=$DATA_ROOT/kafka-data" \
        --set-string "minio.persistence.hostPath=$DATA_ROOT/minio-data" \
        --set-string "postgres.persistence.hostPath=$DATA_ROOT/postgres-data" \
        --set-string "iaWorker.modelCache.hostPath=$DATA_ROOT/model-cache" \
        --set-json "colmapWorker.workVolume.drives=$drives_json" \
        --set-string "colmapWorker.workVolume.default=local" \
        --set-string "colmapWorker.resources.requests.memory=8Gi" \
        --set-string "colmapWorker.resources.limits.memory=${colmap_limit}Gi" \
        --set-string "processingWorker.resources.requests.memory=2Gi" \
        --set-string "processingWorker.resources.limits.memory=${processing_limit}Gi" \
        --set "dashboardFrontend.service.nodePort=$DASHBOARD_PORT" \
        --set "dashboardApi.service.nodePort=$API_PORT" \
        --set "minio.consoleNodePort=$MINIO_CONSOLE_PORT" \
        --set "minio.apiNodePort=$MINIO_API_PORT" \
        --set-string "dashboardFrontend.apiUrl=http://$access_host:$API_PORT" \
        --set-string "storage.s3PublicEndpoint=http://$access_host:$MINIO_API_PORT" \
        "${stage_job_values[@]}" \
        --wait \
        --wait-for-jobs \
        --timeout 10m

    if [[ -n "${STAGE_JOBS_IMAGE_TAG:-}" ]]; then
        kube rollout restart deployment/dashboard-api deployment/dashboard-frontend \
            --namespace drone-ai
        kube wait --for=condition=Available \
            deployment/dashboard-api deployment/dashboard-frontend \
            --namespace drone-ai --timeout=5m
    else
        kube rollout restart deployment \
            colmap-worker ia-worker processing-worker dashboard-api dashboard-frontend \
            --namespace drone-ai
        kube wait --for=condition=Available deployment --all \
            --namespace drone-ai --timeout=5m
    fi

    wait_for_http "http://$access_host:$API_PORT/" "Dashboard API"
    wait_for_http "http://$access_host:$DASHBOARD_PORT/" "Dashboard"

    local not_ready
    not_ready="$(kube get pods --namespace drone-ai --output=json \
        | jq '[.items[]
            | select(.status.phase == "Running")
            | select(any(.status.containerStatuses[]?; .ready != true))
        ] | length')"
    [[ "$not_ready" == "0" ]] \
        || fatal "One or more DroneAI pods are not ready."

    printf '\n'
    success "DroneAI distributed deployment is ready"
    printf 'Dashboard:     http://%s:%s/\n' "$access_host" "$DASHBOARD_PORT"
    printf 'API:           http://%s:%s/\n' "$access_host" "$API_PORT"
    printf 'MinIO console: http://%s:%s/\n' "$access_host" "$MINIO_CONSOLE_PORT"
    if is_wsl; then
        printf 'WSL note: rerun with --no-build after a WSL restart if its IP changes.\n'
    fi
}
