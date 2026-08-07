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
git_sha="$(git -C "${repo_root}" rev-parse HEAD)"

export KUBECONFIG="${kubeconfig}"
export TERRAFORM_BIN="${terraform_bin}"
export TERRAFORM_DIR="${terraform_dir}"
export KUBERNETES_NAMESPACE="${namespace}"
"${repo_root}/scripts/deploy/bootstrap-ovh-preprod-secrets.sh"

helm lint "${repo_root}/charts/drone-ai" -f "${values}" \
  --set-string "global.imageRegistry=${registry_host}/droneai/" \
  --set-string "storage.s3Endpoint=${s3_endpoint}" \
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}" \
  --set-string "processingWorker.tag=${git_sha}" \
  --set-string "dashboardApi.tag=${git_sha}" \
  --set-string "dashboardFrontend.tag=${git_sha}" >/dev/null

release_args=(
  upgrade --install drone-ai "${repo_root}/charts/drone-ai"
  --namespace "${namespace}" --create-namespace
  -f "${values}"
  --set-string "global.imageRegistry=${registry_host}/droneai/"
  --set-string "storage.s3Endpoint=${s3_endpoint}"
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}"
  --set-string "processingWorker.tag=${git_sha}"
  --set-string "dashboardApi.tag=${git_sha}"
  --set-string "dashboardFrontend.tag=${git_sha}"
)

if [[ "${dry_run}" == "1" ]]; then
  helm "${release_args[@]}" --dry-run=server --hide-secret >/dev/null
  printf 'Server-side Helm dry-run: OK\n'
  exit 0
fi

helm "${release_args[@]}" --atomic --wait --wait-for-jobs --timeout 15m

kubectl -n "${namespace}" get pods,pvc,ingress
