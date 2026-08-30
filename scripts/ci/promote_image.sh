#!/usr/bin/env bash
# Scan, sign and record one immutable release image.

set -euo pipefail

usage() {
    echo "Usage: $0 --name NAME --image REF --digest sha256:... --source-commit SHA" >&2
}

name=""
image=""
digest=""
source_commit=""
while (($#)); do
    case "$1" in
        --name) name="${2:-}"; shift 2 ;;
        --image) image="${2:-}"; shift 2 ;;
        --digest) digest="${2:-}"; shift 2 ;;
        --source-commit) source_commit="${2:-}"; shift 2 ;;
        *) usage; exit 2 ;;
    esac
done

if [[ ! "$name" =~ ^[a-z0-9][a-z0-9._-]*$ ]]; then
    echo "Invalid image name: $name" >&2
    exit 2
fi
if [[ -z "$image" || "$image" =~ [[:space:]] ]]; then
    echo "Invalid image reference" >&2
    exit 2
fi
if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "Invalid OCI digest: $digest" >&2
    exit 2
fi
if [[ ! "$source_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid source commit: $source_commit" >&2
    exit 2
fi

readonly syft_image="anchore/syft:v1.50.0@sha256:1288ea4c8b38767b4e620c1e312c8cb26b6e887a99b4f07ab6cd19fc6f225026"
readonly trivy_image="aquasec/trivy:0.73.0@sha256:7cced7cae583819fc7806d4cbc0dbbc7cad18b99f7d3e235192e6da8c091045c"
readonly evidence_dir="${PROMOTION_EVIDENCE_DIR:-promotion-evidence}"
readonly trivy_cache="${PROMOTION_TRIVY_CACHE_DIR:-.trivy-cache}"
readonly target="${image}@${digest}"

mkdir -p "$evidence_dir" "$trivy_cache"
docker run --rm \
    --volume "${HOME}/.docker:/root/.docker:ro" \
    --volume "${PWD}/${evidence_dir}:/output" \
    "$syft_image" \
    "registry:${target}" \
    --output "cyclonedx-json=/output/${name}.cdx.json"
docker run --rm \
    --volume "${HOME}/.docker:/root/.docker:ro" \
    --volume "${PWD}/${trivy_cache}:/root/.cache" \
    --volume "${PWD}/${evidence_dir}:/output" \
    "$trivy_image" \
    image --image-src remote --scanners vuln --severity HIGH,CRITICAL \
    --format json --output "/output/${name}.trivy.json" \
    "$target"
python3 -m scripts.ci.verify_unfixed_cves \
    --report "${evidence_dir}/${name}.trivy.json" \
    --image "$name" \
    --waivers security/unfixed-cve-waivers.json
docker run --rm \
    --volume "${HOME}/.docker:/root/.docker:ro" \
    --volume "${PWD}/${trivy_cache}:/root/.cache" \
    "$trivy_image" \
    image --image-src remote --scanners vuln --ignore-unfixed \
    --severity HIGH,CRITICAL --exit-code 1 "$target"
cosign sign --yes "$target"
cosign verify \
    --certificate-identity \
      "https://github.com/${GITHUB_REPOSITORY}/.github/workflows/promote-images.yml@${GITHUB_REF}" \
    --certificate-oidc-issuer "https://token.actions.githubusercontent.com" \
    "$target"
python3 tools/promotion_manifest.py record \
    --name "$name" \
    --image "$image" \
    --digest "$digest" \
    --source-commit "$source_commit" \
    --sbom "${evidence_dir}/${name}.cdx.json" \
    --vulnerability-report "${evidence_dir}/${name}.trivy.json" \
    --output "${evidence_dir}/${name}.image.json"
