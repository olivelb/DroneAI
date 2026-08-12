#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
kubeconfig="${KUBECONFIG:-${HOME}/.config/droneai/kubeconfig-preprod.yaml}"
namespace="${KUBERNETES_NAMESPACE:-drone-ai-preprod}"

command -v base64 >/dev/null
command -v jq >/dev/null
command -v kubectl >/dev/null
command -v openssl >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null
test -r "${kubeconfig}"

s3_access_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_access_key_id)"
s3_secret_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_secret_access_key)"
backup_access_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw backup_storage_access_key_id)"
backup_secret_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw backup_storage_secret_access_key)"

export KUBECONFIG="${kubeconfig}"
kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if kubectl -n "${namespace}" get secret drone-ai-postgres >/dev/null 2>&1; then
  postgres_password="$(kubectl -n "${namespace}" get secret drone-ai-postgres \
    -o jsonpath='{.data.password}' | base64 --decode)"
  test -n "${postgres_password}"
else
  postgres_password="$(openssl rand -hex 32)"
  kubectl -n "${namespace}" create secret generic drone-ai-postgres \
    --from-literal="password=${postgres_password}" >/dev/null
fi

database_url="postgresql://droneai:${postgres_password}@postgres.${namespace}.svc.cluster.local:5432/droneai"
kubectl -n "${namespace}" create secret generic drone-ai-storage-preprod \
  --from-literal="s3-access-key=${s3_access_key}" \
  --from-literal="s3-secret-key=${s3_secret_key}" \
  --from-literal="database-url=${database_url}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

kubectl -n "${namespace}" create secret generic drone-ai-backup-preprod \
  --from-literal="s3-access-key=${backup_access_key}" \
  --from-literal="s3-secret-key=${backup_secret_key}" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

organization_id="${DRONEAI_ORGANIZATION_ID:-ovh-preprod}"
if kubectl -n "${namespace}" get secret drone-ai-api-auth >/dev/null 2>&1; then
  api_keys_json="$(kubectl -n "${namespace}" get secret drone-ai-api-auth \
    -o jsonpath='{.data.api-keys\.json}' | base64 --decode \
    | jq -c --arg organization_id "${organization_id}" \
      'map(.organization_id = (.organization_id // $organization_id))')"
  session_secret="$(kubectl -n "${namespace}" get secret drone-ai-api-auth \
    -o jsonpath='{.data.session-secret}' | base64 --decode)"
  test -n "${session_secret}"
  kubectl -n "${namespace}" create secret generic drone-ai-api-auth \
    --from-literal="api-keys.json=${api_keys_json}" \
    --from-literal="session-secret=${session_secret}" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
else
  api_key="$(openssl rand -hex 32)"
  session_secret="$(openssl rand -hex 32)"
  api_keys_json="$(jq -cn \
    --arg key "${api_key}" \
    --arg organization_id "${organization_id}" \
    '[{key:$key,subject:"preprod-admin",role:"admin",organization_id:$organization_id}]')"
  kubectl -n "${namespace}" create secret generic drone-ai-api-auth \
    --from-literal="api-keys.json=${api_keys_json}" \
    --from-literal="session-secret=${session_secret}" >/dev/null
fi

unset api_key api_keys_json backup_access_key backup_secret_key database_url organization_id \
  postgres_password s3_access_key s3_secret_key session_secret
printf 'PostgreSQL, application S3, backup S3 and API Secrets are ready in namespace %s.\n' "${namespace}"
