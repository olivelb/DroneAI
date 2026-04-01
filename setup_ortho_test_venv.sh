#!/bin/bash
set -euo pipefail

VENV_DIR="${VENV_DIR:-/home/olivier/ortho_test_venv}"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu124}"
REQUIREMENTS_FILE="/home/olivier/app1-colmap/requirements-ortho-local.txt"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Missing Python interpreter: $PYTHON_BIN" >&2
    exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV_DIR/bin/python" -m ensurepip --upgrade
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

if ! "$VENV_DIR/bin/pip" install --index-url "$TORCH_INDEX_URL" torch; then
    echo "CUDA torch install failed, falling back to default PyPI torch wheel..." >&2
    "$VENV_DIR/bin/pip" install torch
fi

"$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE"

echo "Local orthomosaic venv ready: $VENV_DIR"
echo "Run with: /home/olivier/run_local_ortho.sh --workspace /mnt/j/workspace/vol_banyuls"
