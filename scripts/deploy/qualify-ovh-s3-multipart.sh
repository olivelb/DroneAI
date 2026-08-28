#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
python_bin="${PYTHON_BIN:-${repo_root}/.venv/bin/python}"
backend_env="${TERRAFORM_BACKEND_ENV:-${HOME}/.config/droneai/terraform-backend.env}"
backend_endpoint="${TERRAFORM_BACKEND_ENDPOINT:-https://s3.gra.io.cloud.ovh.net}"
backend_bucket="${TERRAFORM_BACKEND_BUCKET:-droneai-preprod-tfstate-fe7dc125}"
backend_key="${TERRAFORM_BACKEND_KEY:-preprod/terraform.tfstate}"
aws_cli_image="${AWS_CLI_IMAGE:-amazon/aws-cli:2.27.49}"

: "${DRONEAI_QUALIFICATION_ORGANIZATION_ID:?Set the organization owning the temporary CAS probe}"

test -x "${python_bin}"

if test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null; then
  export S3_ACCESS_KEY
  S3_ACCESS_KEY="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_access_key_id)"
  export S3_SECRET_KEY
  S3_SECRET_KEY="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_secret_access_key)"
  export S3_BUCKET
  S3_BUCKET="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_bucket)"
  export S3_ENDPOINT="${S3_ENDPOINT:-https://s3.gra.io.cloud.ovh.net}"
  export S3_REGION="${S3_REGION:-gra}"
else
  command -v docker >/dev/null
  command -v jq >/dev/null
  test -r "${backend_env}"
  # shellcheck disable=SC1090
  source "${backend_env}"
  state_file="$(mktemp)"
  chmod 0600 "${state_file}"
  cleanup_state() {
    rm -f -- "${state_file}"
  }
  trap cleanup_state EXIT
  docker run --rm -i \
    -e AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY \
    -e AWS_REGION \
    -e AWS_DEFAULT_REGION \
    "${aws_cli_image}" \
    --endpoint-url "${backend_endpoint}" \
    s3 cp "s3://${backend_bucket}/${backend_key}" - --only-show-errors \
    >"${state_file}"
  export S3_ACCESS_KEY
  S3_ACCESS_KEY="$(jq -er '.outputs.object_storage_access_key_id.value' "${state_file}")"
  export S3_SECRET_KEY
  S3_SECRET_KEY="$(jq -er '.outputs.object_storage_secret_access_key.value' "${state_file}")"
  export S3_BUCKET
  S3_BUCKET="$(jq -er '.outputs.object_storage_bucket.value' "${state_file}")"
  export S3_ENDPOINT
  S3_ENDPOINT="$(jq -er '.outputs.object_storage_endpoint.value' "${state_file}")"
  export S3_REGION="${S3_REGION:-gra}"
fi

cd "${repo_root}"
"${python_bin}" tools/qualify_s3_conditional_multipart.py --size-mib 6 --organization-id "${DRONEAI_QUALIFICATION_ORGANIZATION_ID}"
