#!/usr/bin/env bash
# ============================================================
# setup_deps.sh — Clone external C++ build dependencies
#
# Run once after cloning DroneAI. Downloads the large repos
# that are gitignored and needed by the Docker build:
#   - Ceres Solver      (optimiser, ~30 MB)
#   - COLMAP 4.1.1      (GLOMAP + Caspar + ONNX frontend)
#
# Usage:  bash setup_deps.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ "$#" -ne 0 ]; then
    echo "Usage: bash setup_deps.sh" >&2
    exit 2
fi

# Pinned versions (bump these when upgrading)
CERES_REPO="https://github.com/ceres-solver/ceres-solver.git"
CERES_COMMIT="849f854ff98fa07b5ef966fbd1fe73348d868b08"  # 2.3-dev, cuDSS enabled
COLMAP_REPO="https://github.com/colmap/colmap.git"
COLMAP_TAG="4.1.1"

info() { echo -e "\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33mWARN:\033[0m $*"; }

# ---- Ceres Solver ----
if [ -d "app1-colmap/ceres-solver/.git" ]; then
    current_commit="$(git -C app1-colmap/ceres-solver rev-parse HEAD)"
    if [ "$current_commit" = "$CERES_COMMIT" ]; then
        info "Ceres Solver $CERES_COMMIT already cloned"
    else
        if [ -n "$(git -C app1-colmap/ceres-solver status --porcelain --untracked-files=no)" ]; then
            echo "ERROR: app1-colmap/ceres-solver has tracked modifications; refusing to overwrite them." >&2
            echo "Move or restore those changes, then rerun setup_deps.sh." >&2
            exit 1
        fi
        info "Updating Ceres Solver to $CERES_COMMIT..."
        git -C app1-colmap/ceres-solver fetch --depth 1 origin "$CERES_COMMIT"
        git -C app1-colmap/ceres-solver checkout --detach FETCH_HEAD
    fi
else
    info "Cloning Ceres Solver $CERES_COMMIT..."
    git init app1-colmap/ceres-solver
    git -C app1-colmap/ceres-solver remote add origin "$CERES_REPO"
    git -C app1-colmap/ceres-solver fetch --depth 1 origin "$CERES_COMMIT"
    git -C app1-colmap/ceres-solver checkout --detach FETCH_HEAD
fi
# Ensure bundled abseil submodule is populated (system libabsl is too old for Ceres 2.3+)
cd app1-colmap/ceres-solver
if [ ! -f "third_party/abseil-cpp/CMakeLists.txt" ]; then
    info "Initialising Ceres abseil-cpp submodule..."
    git submodule update --init third_party/abseil-cpp
fi
cd "$SCRIPT_DIR"

# ---- COLMAP ----
if [ -d "app1-colmap/colmap-local/.git" ]; then
    current_tag="$(git -C app1-colmap/colmap-local describe --tags --exact-match 2>/dev/null || true)"
    if [ "$current_tag" = "$COLMAP_TAG" ]; then
        info "COLMAP $COLMAP_TAG already cloned"
    else
        if [ -n "$(git -C app1-colmap/colmap-local status --porcelain --untracked-files=no)" ]; then
            echo "ERROR: app1-colmap/colmap-local has tracked modifications; refusing to overwrite them." >&2
            echo "Move or restore those changes, then rerun setup_deps.sh." >&2
            exit 1
        fi
        info "Updating COLMAP dependency from ${current_tag:-unknown} to $COLMAP_TAG..."
        git -C app1-colmap/colmap-local fetch --depth 1 origin "refs/tags/$COLMAP_TAG"
        git -C app1-colmap/colmap-local checkout --detach FETCH_HEAD
    fi
else
    info "Cloning COLMAP $COLMAP_TAG..."
    git clone --depth 1 --branch "$COLMAP_TAG" "$COLMAP_REPO" app1-colmap/colmap-local
fi

# ---- COLMAP FetchContent deps (pre-downloaded to avoid network during Docker build) ----
POSELIB_URL="https://github.com/PoseLib/PoseLib/archive/fa7280fee27f97aff31ae7f98bab7f583fac7d08.zip"
POSELIB_SHA256="5408d4ae8ce367cb2f076bc6c5f0f6f78abd3573d2c015304b04e46f23455f5b"
FAISS_URL="https://github.com/facebookresearch/faiss/archive/refs/tags/v1.14.1.zip"
FAISS_SHA256="4b1ae7e7a0a46385b4084f0e3945623a15fcf99d793bf44d82aae8e24f11e5f5"
ONNX_URL="https://github.com/microsoft/onnxruntime/releases/download/v1.24.4/onnxruntime-linux-x64-gpu-1.24.4.tgz"
ONNX_SHA256="c5f804ff5d239b436fa59e9f2fb288a39f7eb9552f6a636c8b71e792e91a8808"
COLMAP_MODEL_BASE_URL="https://github.com/colmap/colmap/releases/download/3.13.0"
ALIKED_N16ROT_SHA256="39c423d0a6f03d39ec89d3d1d61853765c2fb6a8b8381376c703e5758778a547"
ALIKED_N32_SHA256="a077728a02d2de1a775c66df6de8cfeb7c6b51ca57572c64c680131c988c8b3c"
ALIKED_LIGHTGLUE_SHA256="b9a5de7204648b18a8cf5dcac819f9d30de1a5961ef03756803c8b86c2dceb8d"
SIFT_LIGHTGLUE_SHA256="e0500228472b43f92b3d36881a09b3310d3b058b56187b246cc7b9ab6429096e"

download_verified() {
    local url="$1"
    local destination="$2"
    local expected_sha256="$3"
    local label="$4"

    if [ -f "$destination" ] \
        && echo "$expected_sha256  $destination" | sha256sum --check --status; then
        info "$label already downloaded and verified"
        return
    fi
    if [ -f "$destination" ]; then
        warn "$label cache checksum mismatch; replacing the generated dependency cache."
        rm -f -- "$destination"
    fi
    info "Downloading $label..."
    curl --fail --location --retry 3 --silent --show-error "$url" -o "$destination"
    echo "$expected_sha256  $destination" | sha256sum --check --status
}

mkdir -p app1-colmap/colmap-deps
download_verified "$POSELIB_URL" "app1-colmap/colmap-deps/poselib.zip" "$POSELIB_SHA256" "PoseLib"
download_verified "$FAISS_URL" "app1-colmap/colmap-deps/faiss.zip" "$FAISS_SHA256" "faiss"
download_verified "$ONNX_URL" "app1-colmap/colmap-deps/onnxruntime.tgz" "$ONNX_SHA256" "ONNX Runtime GPU"
download_verified "$COLMAP_MODEL_BASE_URL/aliked-n16rot.onnx" "app1-colmap/colmap-deps/aliked-n16rot.onnx" "$ALIKED_N16ROT_SHA256" "ALIKED N16Rot"
download_verified "$COLMAP_MODEL_BASE_URL/aliked-n32.onnx" "app1-colmap/colmap-deps/aliked-n32.onnx" "$ALIKED_N32_SHA256" "ALIKED N32"
download_verified "$COLMAP_MODEL_BASE_URL/aliked-lightglue.onnx" "app1-colmap/colmap-deps/aliked-lightglue.onnx" "$ALIKED_LIGHTGLUE_SHA256" "ALIKED LightGlue"
download_verified "$COLMAP_MODEL_BASE_URL/sift-lightglue.onnx" "app1-colmap/colmap-deps/sift-lightglue.onnx" "$SIFT_LIGHTGLUE_SHA256" "SIFT LightGlue"

info "All dependencies ready. You can now run:"
info "  ./deploy.sh local"
info "  ./deploy.sh distributed"
