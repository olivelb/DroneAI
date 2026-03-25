FROM ultralytics/ultralytics:latest

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV AERIAL_MODEL_DIR=/opt/modelzoo
ENV AERIAL_MODEL_VARIANT=s
ENV AERIAL_MODEL_IMGSZ=1024
ENV AERIAL_MODEL_RELEASE=v8.4.0

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip \
    && python3 -m pip install --no-cache-dir rasterio pyproj confluent-kafka \
        transformers accelerate huggingface_hub safetensors sentencepiece

RUN mkdir -p /opt/modelzoo \
    && python3 - <<'PY'
from pathlib import Path
from urllib.request import urlretrieve

checkpoint_url = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s-obb.pt"
checkpoint_path = Path("/opt/modelzoo/yolo26s-obb.pt")
if not checkpoint_path.exists():
    urlretrieve(checkpoint_url, checkpoint_path)
PY

COPY shared/ /app/shared/
COPY app2-ia/ /app/app2-ia/

CMD ["python3", "app2-ia/main.py"]
