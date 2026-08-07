#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s [--check] <zone> <expected-IPv4> <subdomain>...\n' "${0##*/}" >&2
}

check_only=false
if [[ "${1:-}" == "--check" ]]; then
  check_only=true
  shift
fi

if [[ $# -lt 3 ]]; then
  usage
  exit 2
fi

zone=$1
expected_target=$2
shift 2
subdomains=("$@")

if [[ ! ${expected_target} =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  printf 'Invalid expected IPv4 address: %s\n' "${expected_target}" >&2
  exit 2
fi

: "${OVH_ENDPOINT:?OVH_ENDPOINT is required}"
: "${OVH_APPLICATION_KEY:?OVH_APPLICATION_KEY is required}"
: "${OVH_APPLICATION_SECRET:?OVH_APPLICATION_SECRET is required}"
: "${OVH_CONSUMER_KEY:?OVH_CONSUMER_KEY is required}"

case "${OVH_ENDPOINT}" in
  ovh-eu) api_base=https://eu.api.ovh.com/1.0 ;;
  *)
    printf 'Unsupported OVH endpoint for this deployment: %s\n' "${OVH_ENDPOINT}" >&2
    exit 2
    ;;
esac

api_call() {
  local method=$1
  local path=$2
  local body=${3:-}
  local timestamp signature url

  url="${api_base}${path}"
  timestamp=$(curl --fail --silent --show-error "${api_base}/auth/time")
  signature="\$1\$$(printf '%s' \
    "${OVH_APPLICATION_SECRET}+${OVH_CONSUMER_KEY}+${method}+${url}+${body}+${timestamp}" \
    | sha1sum | cut -d' ' -f1)"

  curl --fail-with-body --silent --show-error \
    --request "${method}" \
    --header "X-Ovh-Application: ${OVH_APPLICATION_KEY}" \
    --header "X-Ovh-Consumer: ${OVH_CONSUMER_KEY}" \
    --header "X-Ovh-Signature: ${signature}" \
    --header "X-Ovh-Timestamp: ${timestamp}" \
    --header 'Content-Type: application/json' \
    --data "${body}" \
    "${url}"
}

changed=false
for subdomain in "${subdomains[@]}"; do
  query_path="/domain/zone/${zone}/record?fieldType=A&subDomain=${subdomain}"
  record_ids=$(api_call GET "${query_path}")
  record_count=$(jq 'length' <<<"${record_ids}")

  if [[ ${record_count} -gt 1 ]]; then
    printf 'Refusing to delete %s.%s: %s A records exist.\n' \
      "${subdomain}" "${zone}" "${record_count}" >&2
    exit 1
  fi

  if [[ ${record_count} -eq 0 ]]; then
    printf 'ABSENT %s.%s\n' "${subdomain}" "${zone}"
    continue
  fi

  record_id=$(jq -r '.[0]' <<<"${record_ids}")
  record=$(api_call GET "/domain/zone/${zone}/record/${record_id}")
  current_target=$(jq -r '.target' <<<"${record}")
  if [[ ${current_target} != "${expected_target}" ]]; then
    printf 'Refusing to delete %s.%s: target is %s, expected %s.\n' \
      "${subdomain}" "${zone}" "${current_target}" "${expected_target}" >&2
    exit 1
  fi

  printf '%s %s.%s -> %s\n' \
    "$([[ ${check_only} == true ]] && printf WOULD-DELETE || printf DELETE)" \
    "${subdomain}" "${zone}" "${current_target}"
  if [[ ${check_only} == false ]]; then
    api_call DELETE "/domain/zone/${zone}/record/${record_id}" >/dev/null
    changed=true
  fi
done

if [[ ${changed} == true ]]; then
  api_call POST "/domain/zone/${zone}/refresh" '{}' >/dev/null
  printf 'Refreshed OVH DNS zone %s.\n' "${zone}"
fi
