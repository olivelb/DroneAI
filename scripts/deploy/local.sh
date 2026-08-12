#!/usr/bin/env bash

deploy_local() {
    info "Deploying the complete local pipeline with Docker Compose"

    "${SUDO[@]}" install -d --mode=0770 --owner=10001 --group=10001 \
        "$DATA_ROOT/colmap-work"

    local drives_json work_drive_override
    drives_json="$(discover_work_drives)"
    work_drive_override="$DATA_ROOT/compose.work-drives.json"
    jq --indent 2 --null-input \
        --argjson drives "$drives_json" \
        '{
          "services": {
            "colmap-worker": {
              "volumes": ($drives | map(.hostPath + ":/work/" + .name))
            }
          }
        }' >"$work_drive_override"

    export DRONEAI_DASHBOARD_PORT="$DASHBOARD_PORT"
    export DRONEAI_API_PORT="$API_PORT"
    export DRONEAI_MINIO_CONSOLE_PORT="$MINIO_CONSOLE_PORT"
    export DRONEAI_MINIO_API_PORT="$MINIO_API_PORT"
    export DRONEAI_ACCESS_HOST="localhost"
    export DRONEAI_DATA_ROOT="$DATA_ROOT"
    export DRONEAI_WORK_DRIVES_JSON
    DRONEAI_WORK_DRIVES_JSON="$(jq --compact-output \
        'map({name, label, mount: ("/work/" + .name)})' <<<"$drives_json")"
    export DRONEAI_WORK_DRIVE_DEFAULT=local
    export HF_TOKEN="${HF_TOKEN:-}"
    info "Work drives: $(jq --raw-output 'map(.label) | join(", ")' <<<"$drives_json")"

    local compose=(
        "${DOCKER[@]}" compose
        --project-name droneai-local
        --file "$REPO_ROOT/compose.local.yaml"
        --file "$work_drive_override"
    )

    "${compose[@]}" up \
        --detach \
        --remove-orphans \
        --wait \
        --wait-timeout 300

    wait_for_http "http://localhost:$API_PORT/" "Dashboard API"
    wait_for_http "http://localhost:$DASHBOARD_PORT/" "Dashboard"

    local running_services service
    running_services="$("${compose[@]}" ps --status running --services)"
    for service in \
        postgres \
        kafka \
        minio \
        colmap-worker \
        ia-worker \
        processing-worker \
        dashboard-api \
        dashboard-control-worker \
        dashboard-frontend
    do
        grep --fixed-strings --line-regexp --quiet "$service" \
            <<<"$running_services" \
            || fatal "Docker Compose service '$service' is not running."
    done

    printf '\n'
    success "DroneAI local deployment is ready"
    printf 'Dashboard:     http://localhost:%s/\n' "$DASHBOARD_PORT"
    printf 'API:           http://localhost:%s/\n' "$API_PORT"
    printf 'MinIO console: http://localhost:%s/\n' "$MINIO_CONSOLE_PORT"
    printf 'Status:        ./deploy.sh local --no-build\n'
    printf 'Stop:          docker compose -p droneai-local -f compose.local.yaml down\n'
}
