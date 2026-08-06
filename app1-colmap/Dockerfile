FROM drone-colmap-base:latest

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/tmp/droneai-home
ENV XDG_CACHE_HOME=/tmp/droneai-cache
ENV CUDA_CACHE_PATH=/tmp/cuda-cache
WORKDIR /app

COPY shared/ /app/shared/
COPY app1-colmap/ /app/app1-colmap/
USER 10001:10001
CMD ["python3", "app1-colmap/main.py"]
