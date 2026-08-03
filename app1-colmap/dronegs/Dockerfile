FROM nvidia/cuda:12.9.2-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential cmake git libjpeg-dev ninja-build python3 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/app1-colmap/dronegs

CMD ["bash"]
