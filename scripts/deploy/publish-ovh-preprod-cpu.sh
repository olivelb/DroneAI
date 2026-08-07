#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"

command -v docker >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null

registry_host="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_host)"
registry_login="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_bootstrap_login)"
registry_password="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_bootstrap_password)"
git_sha="$(git -C "${repo_root}" rev-parse HEAD)"

cleanup() {
  docker logout "${registry_host}" >/dev/null 2>&1 || true
  unset registry_password
}
trap cleanup EXIT

printf '%s' "${registry_password}" | docker login "${registry_host}" \
  --username "${registry_login}" --password-stdin >/dev/null

INCLUDE_GPU_IMAGES=0 \
  "${repo_root}/scripts/deploy/publish-preprod-images.sh" \
  "${registry_host}/droneai" "${git_sha}"
