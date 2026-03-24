FROM drone-colmap-base:latest

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY shared/ /app/shared/
COPY app1-colmap/ /app/app1-colmap/
CMD ["python3", "app1-colmap/main.py"]
