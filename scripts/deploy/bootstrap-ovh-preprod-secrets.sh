#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' \
  'This bootstrap is retired because OVH preproduction requires independently provisioned API and Stage Job identities.' \
  'Create the Secrets from a password manager or external-secrets controller as documented in docs/OVHCLOUD_PREPROD.md.' \
  'Then run scripts/deploy/verify-ovh-preprod-prerequisites.sh for a read-only check.' >&2
exit 2
