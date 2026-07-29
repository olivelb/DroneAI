#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "setup.sh is kept for compatibility." >&2
echo "Use './deploy.sh distributed' for new installations." >&2
exec "$repo_root/deploy.sh" distributed "$@"
