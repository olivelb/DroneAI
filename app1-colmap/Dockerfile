FROM lichtfeld-runtime:latest AS lichtfeld
FROM drone-colmap-base:latest

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# gsplat lazily imports 'packaging' at first CUDA call
RUN pip3 install --no-cache-dir packaging

# LichtFeld-Studio headless binary + runtime libs (GPL-3.0-or-later, subprocess only)
COPY --from=lichtfeld /opt/lichtfeld/ /opt/lichtfeld/
ENV LD_LIBRARY_PATH="/opt/lichtfeld/lib:${LD_LIBRARY_PATH}"
ENV LICHTFELD_BIN="/opt/lichtfeld/LichtFeld-Studio"

COPY shared/ /app/shared/
COPY app1-colmap/ /app/app1-colmap/
CMD ["python3", "app1-colmap/main.py"]
