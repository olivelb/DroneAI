#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
stack_dir="${repo_root}/infra/ovh/preprod"
terraform_bin="${TERRAFORM_BIN:-terraform}"
destination="${1:-${HOME}/.config/droneai/terraform-backend.env}"

mkdir -p "$(dirname "${destination}")"
umask 077

access_key="$("${terraform_bin}" -chdir="${stack_dir}" output -raw terraform_backend_access_key_id)"
secret_key="$("${terraform_bin}" -chdir="${stack_dir}" output -raw terraform_backend_secret_access_key)"
region="$("${terraform_bin}" -chdir="${stack_dir}" output -json | jq -r '.terraform_state_endpoint.value | capture("s3\\.(?<region>[^.]+)\\.").region | ascii_downcase')"
temporary="$(mktemp "${destination}.tmp.XXXXXX")"
trap 'rm -f "${temporary}"' EXIT

{
  printf 'export AWS_ACCESS_KEY_ID=%q\n' "${access_key}"
  printf 'export AWS_SECRET_ACCESS_KEY=%q\n' "${secret_key}"
  printf 'export AWS_REGION=%q\n' "${region}"
  printf 'export AWS_DEFAULT_REGION=%q\n' "${region}"
} >"${temporary}"

chmod 0600 "${temporary}"
mv -f "${temporary}" "${destination}"
trap - EXIT

printf 'Terraform backend credentials written to %s (mode 0600).\n' "${destination}"
