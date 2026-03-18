FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ultralytics rasterio pyproj confluent-kafka

# Pre-load the YOLO model
RUN python3 -c "from ultralytics import YOLO; YOLO('yolo11n-seg.pt')"

COPY app2-ia/ /app/app2-ia/

CMD ["python3", "app2-ia/main.py"]
