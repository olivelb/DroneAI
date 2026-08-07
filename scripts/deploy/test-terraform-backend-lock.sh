#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
stack_dir="${repo_root}/infra/ovh/preprod"
terraform_bin="${TERRAFORM_BIN:-terraform}"
holder_log="$(mktemp /tmp/droneai-tf-lock-holder.XXXXXX)"
contender_log="$(mktemp /tmp/droneai-tf-lock-contender.XXXXXX)"
trap 'rm -f "${holder_log}" "${contender_log}"' EXIT

"${terraform_bin}" -chdir="${stack_dir}" plan \
  -input=false -lock-timeout=30s >"${holder_log}" 2>&1 &
holder_pid=$!

holder_started=false
for _ in {1..200}; do
  if grep -q "Refreshing state" "${holder_log}"; then
    holder_started=true
    break
  fi
  if ! kill -0 "${holder_pid}" 2>/dev/null; then
    break
  fi
  sleep 0.025
done

if [[ "${holder_started}" != true ]]; then
  wait "${holder_pid}" || true
  printf 'The lock-holder plan did not reach state refresh.\n' >&2
  sed -n '1,160p' "${holder_log}" >&2
  exit 1
fi

set +e
"${terraform_bin}" -chdir="${stack_dir}" plan \
  -input=false -lock-timeout=0s >"${contender_log}" 2>&1
contender_status=$?
set -e

wait "${holder_pid}"

if [[ ${contender_status} -eq 0 ]] || \
  ! grep -q "Error acquiring the state lock" "${contender_log}"; then
  printf 'The concurrent plan was not rejected by the S3 state lock.\n' >&2
  sed -n '1,160p' "${contender_log}" >&2
  exit 1
fi

printf 'S3 backend lock contention verified: the concurrent plan was rejected.\n'
