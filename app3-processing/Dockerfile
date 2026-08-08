FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/processing.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
    --requirement /tmp/requirements.txt

RUN groupadd --gid 10001 droneai \
    && useradd --uid 10001 --gid 10001 --create-home droneai

COPY --chown=10001:10001 shared/ /app/shared/
COPY --chown=10001:10001 app3-processing/ /app/app3-processing/

USER 10001:10001

CMD ["python3", "app3-processing/main.py"]
