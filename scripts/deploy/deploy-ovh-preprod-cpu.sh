#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
kubeconfig="${KUBECONFIG:-${HOME}/.config/droneai/kubeconfig-preprod.yaml}"
values="${repo_root}/charts/drone-ai/values-ovh-preprod.example.yaml"
namespace="${KUBERNETES_NAMESPACE:-drone-ai-preprod}"
dry_run="${DRY_RUN:-0}"

command -v helm >/dev/null
command -v kubectl >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null
test -r "${kubeconfig}"

registry_host="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_host)"
s3_endpoint="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_endpoint)"
backup_bucket="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw backup_storage_bucket)"

export KUBECONFIG="${kubeconfig}"
export TERRAFORM_BIN="${terraform_bin}"
export TERRAFORM_DIR="${terraform_dir}"
export KUBERNETES_NAMESPACE="${namespace}"

image_tag="${IMAGE_TAG:-}"
if [[ -z "${image_tag}" ]]; then
  if helm status drone-ai --namespace "${namespace}" >/dev/null 2>&1; then
    deployed_tags=()
    for deployment in processing-worker dashboard-api dashboard-frontend; do
      image_ref="$(kubectl -n "${namespace}" get deployment "${deployment}" \
        -o jsonpath='{.spec.template.spec.containers[0].image}')"
      deployed_tags+=("${image_ref##*:}")
    done
    image_tag="${deployed_tags[0]}"
    for deployed_tag in "${deployed_tags[@]}"; do
      [[ "${deployed_tag}" == "${image_tag}" ]] || {
        printf 'Deployed CPU image tags differ; set IMAGE_TAG explicitly.\n' >&2
        exit 1
      }
    done
    printf 'Reusing deployed CPU image tag %s.\n' "${image_tag}"
  else
    image_tag="$(git -C "${repo_root}" rev-parse HEAD)"
    printf 'No existing release; using current Git SHA %s.\n' "${image_tag}"
  fi
fi
[[ "${image_tag}" =~ ^[0-9a-f]{7,40}$ ]] || {
  printf 'IMAGE_TAG must be a 7-40 character lower-case Git SHA.\n' >&2
  exit 2
}

"${repo_root}/scripts/deploy/bootstrap-ovh-preprod-secrets.sh"

helm lint "${repo_root}/charts/drone-ai" -f "${values}" \
  --set-string "global.imageRegistry=${registry_host}/droneai/" \
  --set-string "storage.s3Endpoint=${s3_endpoint}" \
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Bucket=${backup_bucket}" \
  --set-string "processingWorker.tag=${image_tag}" \
  --set-string "dashboardApi.tag=${image_tag}" \
  --set-string "dashboardFrontend.tag=${image_tag}" >/dev/null

release_args=(
  upgrade --install drone-ai "${repo_root}/charts/drone-ai"
  --namespace "${namespace}" --create-namespace
  -f "${values}"
  --set-string "global.imageRegistry=${registry_host}/droneai/"
  --set-string "storage.s3Endpoint=${s3_endpoint}"
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Bucket=${backup_bucket}"
  --set-string "processingWorker.tag=${image_tag}"
  --set-string "dashboardApi.tag=${image_tag}"
  --set-string "dashboardFrontend.tag=${image_tag}"
)

if [[ "${dry_run}" == "1" ]]; then
  helm "${release_args[@]}" --dry-run=server --hide-secret >/dev/null
  printf 'Server-side Helm dry-run: OK\n'
  exit 0
fi

helm "${release_args[@]}" --atomic --wait --wait-for-jobs --timeout 15m

kubectl -n "${namespace}" get pods,pvc,ingress
