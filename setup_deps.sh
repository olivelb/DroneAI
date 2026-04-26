#!/usr/bin/env bash
# ============================================================
# setup_deps.sh — Clone external C++ build dependencies
#
# Run once after cloning DroneAI. Downloads the large repos
# that are gitignored and needed by the Docker build:
#   - LichtFeld-Studio  (3DGS trainer, ~200 MB)
#   - vcpkg             (C++ package manager, ~300 MB)
#   - Ceres Solver      (optimiser, ~30 MB)
#   - COLMAP            (SfM pipeline, ~60 MB)
#
# Usage:  bash setup_deps.sh
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Pinned versions (bump these when upgrading)
LICHTFELD_REPO="https://github.com/MrNeRF/LichtFeld-Studio.git"
LICHTFELD_COMMIT="1004c0841a3776e3f67866ff34101fbc9677397f"
VCPKG_REPO="https://github.com/microsoft/vcpkg.git"
VCPKG_TAG="2026.03.18"
CERES_REPO="https://github.com/ceres-solver/ceres-solver.git"
CERES_TAG="master"  # >= 2.3.0 required for cuDSS GPU sparse solvers
COLMAP_REPO="https://github.com/colmap/colmap.git"
COLMAP_TAG="4.0.1"

info() { echo -e "\033[1;34m==>\033[0m $*"; }
warn() { echo -e "\033[1;33mWARN:\033[0m $*"; }

# ---- LichtFeld-Studio ----
if [ -d "LichtFeld-Studio/.git" ]; then
    info "LichtFeld-Studio already cloned — checking commit"
    cd LichtFeld-Studio
    CURRENT=$(git rev-parse HEAD)
    if [ "$CURRENT" != "$LICHTFELD_COMMIT" ]; then
        warn "Expected commit $LICHTFELD_COMMIT but found $CURRENT"
        warn "Run: cd LichtFeld-Studio && git fetch && git checkout $LICHTFELD_COMMIT"
    fi
    cd "$SCRIPT_DIR"
else
    info "Cloning LichtFeld-Studio..."
    git clone "$LICHTFELD_REPO" LichtFeld-Studio
    cd LichtFeld-Studio
    git checkout "$LICHTFELD_COMMIT"
    git submodule update --init --recursive
    cd "$SCRIPT_DIR"
fi

# Apply headless + pipeline-minimal patch if not already applied
cd LichtFeld-Studio
if ! grep -q 'LFS_BUILD_PIPELINE_MINIMAL' CMakeLists.txt; then
    if grep -q 'LFS_BUILD_HEADLESS_ONLY' CMakeLists.txt; then
        warn "Old headless-only patch detected — resetting before applying pipeline-minimal patch"
        git checkout -- .
    fi
    info "Applying pipeline-minimal build patch (includes headless support)..."
    git apply "$SCRIPT_DIR/app1-colmap/patches/lichtfeld-pipeline-minimal.patch"
else
    info "Pipeline-minimal patch already applied"
fi
cd "$SCRIPT_DIR"

# ---- vcpkg (for Docker COPY) ----
if [ -d ".docker-vcpkg/.git" ]; then
    info "vcpkg (.docker-vcpkg) already cloned"
else
    info "Cloning vcpkg..."
    git clone --branch "$VCPKG_TAG" "$VCPKG_REPO" .docker-vcpkg
fi

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
