FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/api.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt

COPY shared/ /app/shared/
# Ensure we copy the whole app4-dashboard/api to maintain import paths
COPY app4-dashboard/api/ /app/app4-dashboard/api/

CMD ["uvicorn", "app4-dashboard.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
