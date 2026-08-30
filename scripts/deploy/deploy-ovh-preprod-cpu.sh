#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
kubeconfig="${KUBECONFIG:-${HOME}/.config/droneai/kubeconfig-preprod.yaml}"
values="${repo_root}/charts/drone-ai/values-ovh-preprod.example.yaml"
namespace="${KUBERNETES_NAMESPACE:-drone-ai-preprod}"
dry_run="${DRY_RUN:-0}"
if [[ -n "${IMAGE_TAG:-}" ]]; then
  printf 'IMAGE_TAG is retired for cloud deployment; supply API_IMAGE and FRONTEND_IMAGE digests.\n' >&2
  exit 2
fi
release_values_file="${RELEASE_VALUES_FILE:-}"
if [[ -z "${release_values_file}" || ! -r "${release_values_file}" ]]; then
  printf 'RELEASE_VALUES_FILE is required and must contain every qualified executor image digest.\n' >&2
  exit 2
fi
extra_values=(-f "${release_values_file}")

command -v helm >/dev/null
command -v kubectl >/dev/null
command -v python3 >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null
test -r "${kubeconfig}"

s3_endpoint="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_endpoint)"
backup_bucket="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw backup_storage_bucket)"

export KUBECONFIG="${kubeconfig}"
export TERRAFORM_BIN="${terraform_bin}"
export TERRAFORM_DIR="${terraform_dir}"
export KUBERNETES_NAMESPACE="${namespace}"

api_image="${API_IMAGE:-}"
frontend_image="${FRONTEND_IMAGE:-}"
if [[ -n "${api_image}" || -n "${frontend_image}" ]]; then
  if [[ -z "${api_image}" || -z "${frontend_image}" ]]; then
    printf 'API_IMAGE and FRONTEND_IMAGE must be supplied together.\n' >&2
    exit 2
  fi
  for image_ref in "${api_image}" "${frontend_image}"; do
    [[ "${image_ref}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || {
      printf 'API_IMAGE and FRONTEND_IMAGE must use full OCI digests (registry/repository@sha256:...).\n' >&2
      exit 2
    }
  done
  image_overrides=(
    --set-string "dashboardApi.image=${api_image}"
    --set-string "dashboardFrontend.image=${frontend_image}"
  )
else
  image_overrides=()
fi

"${repo_root}/scripts/deploy/verify-ovh-preprod-prerequisites.sh"

helm lint "${repo_root}/charts/drone-ai" -f "${values}" "${extra_values[@]}" \
  --set-string "global.imageRegistry=" \
  --set-string "storage.s3Endpoint=${s3_endpoint}" \
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Bucket=${backup_bucket}" \
  "${image_overrides[@]}" >/dev/null

release_args=(
  upgrade --install drone-ai "${repo_root}/charts/drone-ai"
  --namespace "${namespace}"
  -f "${values}" "${extra_values[@]}"
  --set-string "global.imageRegistry="
  --set-string "storage.s3Endpoint=${s3_endpoint}"
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Bucket=${backup_bucket}"
  "${image_overrides[@]}"
)

if [[ "${dry_run}" == "1" ]]; then
  helm "${release_args[@]}" --dry-run=server --hide-secret >/dev/null
  printf 'Server-side Helm dry-run: OK\n'
  exit 0
fi

helm "${release_args[@]}" --atomic --wait --wait-for-jobs --timeout 15m

kubectl -n "${namespace}" get pods,pvc,ingress
