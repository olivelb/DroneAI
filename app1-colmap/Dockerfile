FROM drone-colmap-base:latest

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# gsplat lazily imports 'packaging' at first CUDA call
RUN pip3 install --no-cache-dir --break-system-packages packaging

COPY shared/ /app/shared/
COPY app1-colmap/ /app/app1-colmap/
CMD ["python3", "app1-colmap/main.py"]
