#!/usr/bin/env bash
# -----------------------------------------------------------------
#  Setup virtual environment for Gaussian Splatting Orthomosaic
#  (Tortho-Gaussian-inspired TDOM generation)
#
#  Creates: ~/gaussian_ortho_venv
#  Requires: Python 3.12, CUDA toolkit (for rasterizer build)
# -----------------------------------------------------------------
set -euo pipefail

VENV_DIR="${HOME}/gaussian_ortho_venv"
PYTHON_BIN="python3.12"

echo "=== Gaussian Ortho venv setup ==="

# ---- Create venv ----
if [[ ! -d "${VENV_DIR}" ]]; then
    echo "Creating venv at ${VENV_DIR}..."
    ${PYTHON_BIN} -m venv "${VENV_DIR}"
else
    echo "Venv already exists at ${VENV_DIR}"
fi

PIP="${VENV_DIR}/bin/pip"
PY="${VENV_DIR}/bin/python"

${PIP} install --upgrade pip setuptools wheel

# ---- PyTorch + CUDA ----
echo "Installing PyTorch with CUDA 12.4..."
${PIP} install torch torchvision --index-url https://download.pytorch.org/whl/cu124 2>/dev/null \
    || { echo "CUDA 12.4 wheels unavailable, falling back to CPU..."; \
         ${PIP} install torch torchvision --index-url https://download.pytorch.org/whl/cpu; }

# ---- Core Python deps ----
echo "Installing Python dependencies..."
${PIP} install -r "${HOME}/requirements-gaussian-ortho.txt"

# ---- Build CUDA rasterizers from Tortho_Gaussian ----
RAST_DIR="${HOME}/gaussian_ortho_rasterizers"
if [[ ! -d "${RAST_DIR}" ]]; then
    echo "Cloning Tortho_Gaussian for rasterizer submodules..."
    mkdir -p "${RAST_DIR}"
    cd "${RAST_DIR}"
    git clone --depth 1 https://github.com/xwangSGG/Tortho_Gaussian.git repo
    cd repo

    echo "Building perspective rasterizer (diff-gaussian-rasterization)..."
    ${PIP} install submodules/diff-gaussian-rasterization \
        || echo "WARNING: perspective rasterizer build failed — CUDA toolkit required"

    echo "Building orthographic rasterizer (diff-gaussian-rasterization-ortho)..."
    ${PIP} install submodules/diff-gaussian-rasterization-ortho \
        || echo "WARNING: orthographic rasterizer build failed — CUDA toolkit required"

    cd "${HOME}"
else
    echo "Rasterizer directory already exists at ${RAST_DIR}"
fi

# ---- Verify ----
echo ""
echo "=== Verification ==="
${PY} -c "import torch; print(f'PyTorch {torch.__version__}, CUDA: {torch.cuda.is_available()}')"
${PY} -c "import numpy; print(f'NumPy {numpy.__version__}')"
${PY} -c "import rasterio; print(f'Rasterio {rasterio.__version__}')"
${PY} -c "import pycolmap; print(f'pycolmap OK')" 2>/dev/null || echo "pycolmap not installed (optional for unit tests)"

# Try importing rasterizers — they may not be built yet
${PY} -c "from diff_gaussian_rasterization import GaussianRasterizer; print('Perspective rasterizer OK')" 2>/dev/null \
    || echo "Perspective rasterizer not available (CUDA build needed)"
${PY} -c "from diff_gaussian_rasterization_ortho import GaussianRasterizer; print('Ortho rasterizer OK')" 2>/dev/null \
    || echo "Ortho rasterizer not available (CUDA build needed)"

echo ""
echo "=== Setup complete ==="
echo "Activate: source ${VENV_DIR}/bin/activate"
echo "Run:      python run_local_gaussian_ortho.py --workspace /mnt/j/workspace/vol_banyuls"
