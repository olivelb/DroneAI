FROM nvidia/cuda:12.9.2-base-ubuntu24.04@sha256:bb0d3cf98bade70e5bf3e15d92355d1418b9628f59b7ed853e83ea66ff20fa49

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH=/opt/venv/bin:$PATH
ENV AERIAL_MODEL_DIR=/opt/modelzoo
ENV AERIAL_BAKED_MODEL_DIR=/opt/modelzoo
ENV AERIAL_MODEL_VARIANT=best
ENV AERIAL_MODEL_IMGSZ=1024
ENV AERIAL_MODEL_RELEASE=v8.4.0
ENV AERIAL_MODEL_SHA256=8674b0c24bf68aab5eb45009e0ac3808ce432237edf8cb5c50ae2191cb263a2b

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ia-extra.txt /tmp/requirements.txt
RUN python3 -m venv /opt/venv \
    && python3 -m pip install --no-cache-dir --require-hashes --requirement /tmp/requirements.txt \
    && python3 -m pip check

RUN mkdir -p /opt/modelzoo \
    && python3 - <<'PY'
from pathlib import Path
from hashlib import sha256
from os import environ
from urllib.request import urlretrieve

checkpoint_url = "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26l-obb.pt"
checkpoint_path = Path("/opt/modelzoo/yolo26l-obb.pt")
if not checkpoint_path.exists():
    urlretrieve(checkpoint_url, checkpoint_path)
digest = sha256(checkpoint_path.read_bytes()).hexdigest()
expected = environ["AERIAL_MODEL_SHA256"]
if digest != expected:
    checkpoint_path.unlink(missing_ok=True)
    raise RuntimeError(
        f"model checksum mismatch: expected {expected}, got {digest}"
    )
PY

RUN groupadd --gid 10001 droneai \
    && useradd --uid 10001 --gid 10001 --create-home droneai \
    && install -d --owner=10001 --group=10001 /cache/huggingface

ENV HOME=/home/droneai
ENV YOLO_CONFIG_DIR=/tmp/Ultralytics

COPY --chown=10001:10001 shared/ /app/shared/
COPY --chown=10001:10001 app2-ia/ /app/app2-ia/

USER 10001:10001

CMD ["python3", "app2-ia/main.py"]
