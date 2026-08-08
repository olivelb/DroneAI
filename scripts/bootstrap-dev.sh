#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

required_commands=("${PYTHON_BIN}" make gcc g++ cmake ninja pkg-config shellcheck zip)
missing_commands=()

for required_command in "${required_commands[@]}"; do
    if ! command -v "${required_command}" >/dev/null 2>&1; then
        missing_commands+=("${required_command}")
    fi
done

if ((${#missing_commands[@]} > 0)); then
    if ! command -v apt-get >/dev/null 2>&1; then
        printf 'Missing tools and no supported package manager: %s\n' "${missing_commands[*]}" >&2
        exit 1
    fi

    apt_prefix=()
    if ((EUID != 0)); then
        if ! command -v sudo >/dev/null 2>&1; then
            printf 'Missing tools require root privileges: %s\n' "${missing_commands[*]}" >&2
            exit 1
        fi
        apt_prefix=(sudo)
    fi

    "${apt_prefix[@]}" apt-get update
    "${apt_prefix[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install \
        --yes --no-install-recommends \
        build-essential \
        cmake \
        libjpeg-dev \
        ninja-build \
        pkg-config \
        python3-dev \
        python3-venv \
        shellcheck \
        zip
fi

"${PYTHON_BIN}" -c '
import sys

if not ((3, 12) <= sys.version_info[:2] < (3, 13)):
    raise SystemExit(
        f"DroneAI requires Python 3.12, found {sys.version.split()[0]}"
    )
'

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --quiet \
    --require-hashes \
    --requirement "${PROJECT_ROOT}/requirements/dev.txt"

(
    cd "${PROJECT_ROOT}"
    # Activating the project environment also exposes the locked actionlint,
    # ShellCheck and Pyflakes executables to their integrations.
    # The activation script is generated at runtime in .venv.
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    make static
)

printf '\nDevelopment environment ready. Activate it with:\n'
printf '  cd %q && source .venv/bin/activate\n' "${PROJECT_ROOT}"
