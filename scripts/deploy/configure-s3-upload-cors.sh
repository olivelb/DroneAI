#!/usr/bin/env bash
set -euo pipefail

for name in S3_ENDPOINT S3_BUCKET S3_REGION S3_ACCESS_KEY S3_SECRET_KEY \
  DRONEAI_UPLOAD_ALLOWED_ORIGINS; do
  if [[ -z "${!name:-}" ]]; then
    echo "Required environment variable is missing: ${name}" >&2
    exit 1
  fi
done

command -v aws >/dev/null 2>&1 || {
  echo "aws CLI is required" >&2
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "jq is required" >&2
  exit 1
}

cors_file="$(mktemp)"
trap 'rm -f -- "$cors_file"' EXIT

jq -n \
  --arg origins "$DRONEAI_UPLOAD_ALLOWED_ORIGINS" \
  '{
    CORSRules: [{
      AllowedOrigins: ($origins | split(",") | map(gsub("^\\s+|\\s+$"; "")) | map(select(length > 0))),
      AllowedMethods: ["PUT", "GET", "HEAD"],
      AllowedHeaders: ["*"],
      ExposeHeaders: ["ETag"],
      MaxAgeSeconds: 3600
    }]
  }' > "$cors_file"

if [[ "$(jq '.CORSRules[0].AllowedOrigins | length' "$cors_file")" -eq 0 ]]; then
  echo "DRONEAI_UPLOAD_ALLOWED_ORIGINS contains no origin" >&2
  exit 1
fi

export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export AWS_EC2_METADATA_DISABLED=true

aws s3api put-bucket-cors \
  --endpoint-url "$S3_ENDPOINT" \
  --region "${S3_REGION,,}" \
  --bucket "$S3_BUCKET" \
  --cors-configuration "file://$cors_file"

echo "Direct-upload CORS configured for bucket ${S3_BUCKET}."
