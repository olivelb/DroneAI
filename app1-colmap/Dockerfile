FROM nvidia/cuda:12.4.1-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# --- LAYER 1: Base Stable Dependencies ---
RUN apt-get update && apt-get install -y \
    cmake ninja-build git build-essential \
    libboost-program-options-dev libboost-filesystem-dev libboost-graph-dev \
    libboost-system-dev libboost-iostreams-dev libgoogle-glog-dev libgflags-dev \
    libsuitesparse-dev libfreeimage-dev libglew-dev libqt5opengl5-dev qtbase5-dev \
    libcgal-dev libatlas-base-dev libeigen3-dev libgmp-dev liblz4-dev libmetis-dev \
    libopencv-dev libopenblas-dev libpng-dev libjpeg-dev libtiff-dev \
    libcudnn9-dev-cuda-12 \
    python3 python3-pip python3-setuptools curl \
    && rm -rf /var/lib/apt/lists/*

# --- LAYER 2: Ceres Solver (Stable) ---
RUN git clone https://github.com/ceres-solver/ceres-solver.git \
    && cd ceres-solver && git checkout 2.2.0 \
    && mkdir build && cd build \
    && cmake .. -GNinja -DBUILD_TESTING=OFF -DBUILD_EXAMPLES=OFF && ninja install \
    && cd ../.. && rm -rf ceres-solver

# --- LAYER 3: Additional Dependencies (Added during debugging) ---
RUN apt-get update && apt-get install -y \
    libopenimageio-dev openimageio-tools libopenexr-dev libsqlite3-dev \
    libflann-dev libssl-dev libxcb-cursor0 libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

# --- LAYER 4: COLMAP 4.0.1 (Iterative Build) ---
RUN git clone https://github.com/colmap/colmap.git \
    && cd colmap && git checkout 4.0.1 \
    && mkdir build && cd build \
    && cmake .. -GNinja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGUI=OFF \
        -DCUDA_ENABLED=ON \
        -DCMAKE_CUDA_ARCHITECTURES="86-real;89-real" \
    && ninja install \
    && cd ../.. && rm -rf colmap

# --- LAYER 5: Python App ---
RUN pip3 install --no-cache-dir \
    confluent-kafka exif rasterio numpy plyfile pyproj trimesh pycolmap==4.0.1

COPY app1-colmap/ /app/app1-colmap/
CMD ["python3", "app1-colmap/main.py"]
