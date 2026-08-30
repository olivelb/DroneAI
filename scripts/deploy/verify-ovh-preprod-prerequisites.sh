#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
kubeconfig="${KUBECONFIG:-${HOME}/.config/droneai/kubeconfig-preprod.yaml}"
namespace="${KUBERNETES_NAMESPACE:-drone-ai-preprod}"

command -v kubectl >/dev/null
command -v python3 >/dev/null
test -r "${kubeconfig}"
cd "${repo_root}"
python3 -m scripts.deploy.verify_preprod_prerequisites \
  --kubeconfig "${kubeconfig}" --namespace "${namespace}"
