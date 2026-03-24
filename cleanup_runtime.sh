#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=false
AUTO_APPROVE=false
TARGET_SCOPE="selected"
NAMESPACES=(kafka)
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    cat <<'EOF'
Usage: cleanup_runtime.sh [options]

Options:
  --dry-run           Show the commands without executing them.
  --yes               Skip the confirmation prompt.
  --namespace NAME    Add a namespace whose completed/failed/evicted pods should be removed.
  --all-namespaces    Clean completed/failed/evicted pods in every namespace.
  --help              Show this help.

This script prunes unused Docker images and builder cache, prunes unused k3s images,
deletes completed/failed/evicted pods, and removes leftover image tar files created by
the local deploy scripts.
EOF
}

run_cmd() {
    if $DRY_RUN; then
        printf '+ %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

run_privileged() {
    if command -v sudo >/dev/null 2>&1; then
        run_cmd sudo "$@"
    else
        run_cmd "$@"
    fi
}

collect_namespaces() {
    if [[ "$1" == "all" ]]; then
        run_privileged kubectl get namespaces -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
    else
        printf '%s\n' "${NAMESPACES[@]}"
    fi
}

delete_phase_pods() {
    local namespace="$1"
    local phase="$2"
    local pods
    pods="$(run_privileged kubectl get pods -n "$namespace" --field-selector "status.phase=${phase}" -o name 2>/dev/null || true)"
    if [[ -z "$pods" ]]; then
        return 0
    fi
    while IFS= read -r pod; do
        [[ -z "$pod" ]] && continue
        run_privileged kubectl delete -n "$namespace" "$pod" --ignore-not-found=true
    done <<< "$pods"
}

delete_evicted_pods() {
    local namespace="$1"
    local pods
    pods="$(run_privileged kubectl get pods -n "$namespace" --no-headers 2>/dev/null | awk '$3 == "Evicted" {print $1}')"
    if [[ -z "$pods" ]]; then
        return 0
    fi
    while IFS= read -r pod; do
        [[ -z "$pod" ]] && continue
        run_privileged kubectl delete pod -n "$namespace" "$pod" --ignore-not-found=true
    done <<< "$pods"
}

remove_leftover_tars() {
    local pattern
    for pattern in 'drone-*.tar' 'app1-*.tar' 'app2-*.tar' 'app3-*.tar' 'app4-*.tar'; do
        while IFS= read -r tar_file; do
            [[ -z "$tar_file" ]] && continue
            run_cmd rm -f "$tar_file"
        done < <(find "$ROOT_DIR" -maxdepth 1 -type f -name "$pattern" -print)
    done
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --yes)
            AUTO_APPROVE=true
            shift
            ;;
        --namespace)
            [[ $# -lt 2 ]] && { echo "Missing value for --namespace" >&2; exit 1; }
            NAMESPACES+=("$2")
            shift 2
            ;;
        --all-namespaces)
            TARGET_SCOPE="all"
            shift
            ;;
        --help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if ! $AUTO_APPROVE && ! $DRY_RUN; then
    echo "This will prune unused Docker and k3s images, delete completed/failed/evicted pods, and remove leftover deploy tar files under $ROOT_DIR."
    read -r -p "Continue? [y/N] " response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

run_privileged docker container prune -f
run_privileged docker image prune -af
run_privileged docker builder prune -af
run_privileged k3s crictl rmi --prune || true

while IFS= read -r namespace; do
    [[ -z "$namespace" ]] && continue
    delete_phase_pods "$namespace" Succeeded
    delete_phase_pods "$namespace" Failed
    delete_evicted_pods "$namespace"
done < <(collect_namespaces "$TARGET_SCOPE")

remove_leftover_tars

echo "Cleanup completed."