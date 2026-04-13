FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir rasterio confluent-kafka opencv-python-headless \
    boto3 sqlalchemy geoalchemy2 psycopg2-binary

COPY shared/ /app/shared/
COPY app3-processing/ /app/app3-processing/

CMD ["python3", "app3-processing/main.py"]
