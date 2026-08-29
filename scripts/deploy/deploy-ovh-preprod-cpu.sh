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
extra_values=()
if [[ -n "${RELEASE_VALUES_FILE:-}" ]]; then
  test -r "${RELEASE_VALUES_FILE}"
  extra_values=(-f "${RELEASE_VALUES_FILE}")
fi

command -v helm >/dev/null
command -v kubectl >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null
test -r "${kubeconfig}"

s3_endpoint="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_endpoint)"
backup_bucket="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw backup_storage_bucket)"

export KUBECONFIG="${kubeconfig}"
export TERRAFORM_BIN="${terraform_bin}"
export TERRAFORM_DIR="${terraform_dir}"
export KUBERNETES_NAMESPACE="${namespace}"

# An existing release is reused by exact digest. New releases must supply the
# published references; never resolve a mutable Git tag during deployment.
api_image="${API_IMAGE:-}"
frontend_image="${FRONTEND_IMAGE:-}"
if [[ -z "${api_image}" && -z "${frontend_image}" ]]; then
  if ! helm status drone-ai --namespace "${namespace}" >/dev/null 2>&1; then
    printf 'First deployment requires API_IMAGE and FRONTEND_IMAGE digests.\n' >&2
    exit 2
  fi
  api_image="$(kubectl -n "${namespace}" get deployment dashboard-api \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
  control_image="$(kubectl -n "${namespace}" get deployment dashboard-control-worker \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
  frontend_image="$(kubectl -n "${namespace}" get deployment dashboard-frontend \
    -o jsonpath='{.spec.template.spec.containers[0].image}')"
  [[ "${control_image}" == "${api_image}" ]] || {
    printf 'Deployed API/control images differ; supply both image digests explicitly.\n' >&2
    exit 1
  }
  printf 'Reusing deployed CPU image digests.\n'
fi
for image_ref in "${api_image}" "${frontend_image}"; do
  [[ "${image_ref}" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] || {
    printf 'API_IMAGE and FRONTEND_IMAGE must use full OCI digests (registry/repository@sha256:...).\n' >&2
    exit 2
  }
done

"${repo_root}/scripts/deploy/bootstrap-ovh-preprod-secrets.sh"

helm lint "${repo_root}/charts/drone-ai" -f "${values}" "${extra_values[@]}" \
  --set-string "global.imageRegistry=" \
  --set-string "storage.s3Endpoint=${s3_endpoint}" \
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}" \
  --set-string "postgres.backup.s3Bucket=${backup_bucket}" \
  --set-string "dashboardApi.image=${api_image}" \
  --set-string "dashboardFrontend.image=${frontend_image}" >/dev/null

release_args=(
  upgrade --install drone-ai "${repo_root}/charts/drone-ai"
  --namespace "${namespace}" --create-namespace
  -f "${values}" "${extra_values[@]}"
  --set-string "global.imageRegistry="
  --set-string "storage.s3Endpoint=${s3_endpoint}"
  --set-string "storage.s3PublicEndpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Endpoint=${s3_endpoint}"
  --set-string "postgres.backup.s3Bucket=${backup_bucket}"
  --set-string "dashboardApi.image=${api_image}"
  --set-string "dashboardFrontend.image=${frontend_image}"
)

if [[ "${dry_run}" == "1" ]]; then
  helm "${release_args[@]}" --dry-run=server --hide-secret >/dev/null
  printf 'Server-side Helm dry-run: OK\n'
  exit 0
fi

helm "${release_args[@]}" --atomic --wait --wait-for-jobs --timeout 15m

kubectl -n "${namespace}" get pods,pvc,ingress
