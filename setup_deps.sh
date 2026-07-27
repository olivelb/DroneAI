#!/usr/bin/env bash
# ============================================================
# setup_deps.sh — Clone external C++ build dependencies
#
# Run once after cloning DroneAI. Downloads the large repos
# that are gitignored and needed by the Docker build:
#   - Ceres Solver      (optimiser, ~30 MB)
#   - COLMAP            (SfM pipeline, ~60 MB)
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
CERES_TAG="master"  # >= 2.3.0 required for cuDSS GPU sparse solvers
COLMAP_REPO="https://github.com/colmap/colmap.git"
COLMAP_TAG="4.0.1"

info() { echo -e "\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33mWARN:\033[0m $*"; }

# ---- Ceres Solver ----
if [ -d "app1-colmap/ceres-solver/.git" ]; then
    info "Ceres Solver already cloned"
else
    info "Cloning Ceres Solver $CERES_TAG..."
    git clone --depth 1 --branch "$CERES_TAG" "$CERES_REPO" app1-colmap/ceres-solver
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
    info "COLMAP already cloned"
else
    info "Cloning COLMAP $COLMAP_TAG..."
    git clone --depth 1 --branch "$COLMAP_TAG" "$COLMAP_REPO" app1-colmap/colmap-local
fi

# Apply minimal-pipeline patch to COLMAP
cd app1-colmap/colmap-local
if ! grep -q 'COLMAP_MINIMAL_PIPELINE_BUILD' CMakeLists.txt; then
    info "Applying COLMAP minimal-pipeline patch..."
    git apply "$SCRIPT_DIR/app1-colmap/patches/colmap-minimal-pipeline.patch"
else
    info "COLMAP minimal-pipeline patch already applied"
fi
cd "$SCRIPT_DIR"

# ---- COLMAP FetchContent deps (pre-downloaded to avoid network during Docker build) ----
POSELIB_URL="https://github.com/PoseLib/PoseLib/archive/f119951fca625133112acde48daffa5f20eba451.zip"
FAISS_URL="https://github.com/ahojnnes/faiss/archive/36b77353dc435383e0c23a709e7997a29d049041.zip"
mkdir -p app1-colmap/colmap-deps
if [ ! -f "app1-colmap/colmap-deps/poselib.zip" ]; then
    info "Downloading PoseLib..."
    curl -sL "$POSELIB_URL" -o app1-colmap/colmap-deps/poselib.zip
fi
if [ ! -f "app1-colmap/colmap-deps/faiss.zip" ]; then
    info "Downloading faiss..."
    curl -sL "$FAISS_URL" -o app1-colmap/colmap-deps/faiss.zip
fi

info "All dependencies ready. You can now run:"
info "  sudo bash deploy_app1_colmap.sh --base"
