#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${TERRAFORM_DIR:-${repo_root}/infra/ovh/preprod}"
terraform_bin="${TERRAFORM_BIN:-terraform}"
kubeconfig="${KUBECONFIG:-${HOME}/.config/droneai/kubeconfig-preprod.yaml}"
namespace="${KUBERNETES_NAMESPACE:-drone-ai-preprod}"
project="${HARBOR_PROJECT:-droneai}"
pull_robot_short_name="${HARBOR_PULL_ROBOT:-k8s-pull}"

command -v curl >/dev/null
command -v jq >/dev/null
command -v kubectl >/dev/null
test -x "${terraform_bin}" || command -v "${terraform_bin}" >/dev/null
test -r "${kubeconfig}"

registry_host="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_host)"
bootstrap_login="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_bootstrap_login)"
bootstrap_password="$("${terraform_bin}" -chdir="${terraform_dir}" output -raw registry_bootstrap_password)"
harbor_api="https://${registry_host}/api/v2.0"

harbor() {
  curl --silent --show-error --fail-with-body \
    --user "${bootstrap_login}:${bootstrap_password}" \
    -H 'Accept: application/json' "$@"
}

project_result="$(harbor --get --data-urlencode "name=${project}" "${harbor_api}/projects")"
project_count="$(jq --arg project "${project}" '[.[] | select(.name == $project)] | length' <<<"${project_result}")"
if [[ "${project_count}" -eq 0 ]]; then
  harbor -X POST -H 'Content-Type: application/json' \
    --data "$(jq -cn --arg project "${project}" '{project_name:$project,metadata:{public:"false"}}')" \
    "${harbor_api}/projects" >/dev/null
  printf 'Created private Harbor project %s.\n' "${project}"
elif [[ "${project_count}" -eq 1 ]]; then
  public="$(jq -r --arg project "${project}" '.[] | select(.name == $project) | .metadata.public' <<<"${project_result}")"
  test "${public}" = "false"
  printf 'Private Harbor project %s already exists.\n' "${project}"
else
  printf 'Refusing ambiguous Harbor project lookup for %s.\n' "${project}" >&2
  exit 1
fi

robots="$(harbor --get --data-urlencode 'page_size=100' "${harbor_api}/robots")"
robot_count="$(jq --arg project "${project}" --arg name "${pull_robot_short_name}" \
  '[.[] | select(.level == "project" and (.permissions | any(.namespace == $project)) and (.name | endswith("+" + $name)))] | length' \
  <<<"${robots}")"

export KUBECONFIG="${kubeconfig}"
kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

if [[ "${robot_count}" -eq 0 ]]; then
  robot_payload="$(jq -cn \
    --arg project "${project}" \
    --arg name "${pull_robot_short_name}" \
    '{name:$name,description:"DroneAI preprod Kubernetes pull-only account",level:"project",disable:false,duration:-1,permissions:[{kind:"project",namespace:$project,access:[{resource:"repository",action:"pull"}]}]}')"
  robot_created="$(harbor -X POST -H 'Content-Type: application/json' \
    --data "${robot_payload}" "${harbor_api}/robots")"
  robot_name="$(jq -er '.name' <<<"${robot_created}")"
  robot_secret="$(jq -er '.secret' <<<"${robot_created}")"

  curl --silent --show-error --fail --user "${robot_name}:${robot_secret}" \
    "https://${registry_host}/v2/" >/dev/null

  kubectl -n "${namespace}" create secret docker-registry drone-ai-registry \
    --docker-server="${registry_host}" \
    --docker-username="${robot_name}" \
    --docker-password="${robot_secret}" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  unset robot_secret robot_created
  printf 'Created and verified pull-only Harbor robot; Kubernetes pull secret is ready.\n'
elif [[ "${robot_count}" -eq 1 ]]; then
  kubectl -n "${namespace}" get secret drone-ai-registry >/dev/null
  printf 'Harbor pull robot and Kubernetes pull secret already exist.\n'
else
  printf 'Refusing ambiguous Harbor robot lookup for %s.\n' "${pull_robot_short_name}" >&2
  exit 1
fi

unset bootstrap_password
