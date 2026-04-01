#!/bin/bash
set -euo pipefail

VENV_DIR="${VENV_DIR:-/home/olivier/ortho_test_venv}"
PYTHON_BIN="$VENV_DIR/bin/python"
RUNNER="/home/olivier/app1-colmap/run_local_ortho.py"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Missing local orthomosaic venv at $VENV_DIR" >&2
    echo "Run /home/olivier/setup_ortho_test_venv.sh first." >&2
    exit 1
fi

exec "$PYTHON_BIN" "$RUNNER" "$@"