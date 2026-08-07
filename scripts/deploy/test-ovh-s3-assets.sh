#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
aws_cli_image="${AWS_CLI_IMAGE:-amazon/aws-cli:2.27.49}"
endpoint="${S3_ENDPOINT:-https://s3.gra.io.cloud.ovh.net}"

command -v docker >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null

app_access_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_access_key_id)"
app_secret_key="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_secret_access_key)"
bucket="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw object_storage_bucket)"
export AWS_ACCESS_KEY_ID="${app_access_key}"
export AWS_SECRET_ACCESS_KEY="${app_secret_key}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-gra}"
key="healthchecks/codex-$(date -u +%Y%m%dT%H%M%SZ).txt"
expected="droneai-s3-ok"
uploaded=false

aws_cli() {
  docker run --rm -i \
    -e AWS_ACCESS_KEY_ID \
    -e AWS_SECRET_ACCESS_KEY \
    -e AWS_DEFAULT_REGION \
    "${aws_cli_image}" \
    --endpoint-url "${endpoint}" "$@"
}

cleanup() {
  if [[ "${uploaded}" == true ]]; then
    aws_cli s3 rm "s3://${bucket}/${key}" --only-show-errors >/dev/null || true
  fi
}
trap cleanup EXIT

printf '%s\n' "${expected}" | aws_cli s3 cp - "s3://${bucket}/${key}" --only-show-errors
uploaded=true
actual="$(aws_cli s3 cp "s3://${bucket}/${key}" - --only-show-errors)"
test "${actual}" = "${expected}"
aws_cli s3 rm "s3://${bucket}/${key}" --only-show-errors
uploaded=false

printf 'S3 scoped read/write/delete test: OK\n'
