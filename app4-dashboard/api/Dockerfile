FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn[standard] pydantic confluent-kafka websockets

COPY shared/ /app/shared/
# Ensure we copy the whole app4-dashboard/api to maintain import paths
COPY app4-dashboard/api/ /app/app4-dashboard/api/

CMD ["uvicorn", "app4-dashboard.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
