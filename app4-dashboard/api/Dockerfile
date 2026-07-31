FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/api.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/requirements.txt

RUN groupadd --gid 10001 droneai \
    && useradd --uid 10001 --gid 10001 --create-home droneai

COPY --chown=10001:10001 shared/ /app/shared/
COPY --chown=10001:10001 alembic.ini /app/alembic.ini
COPY --chown=10001:10001 alembic/ /app/alembic/
# Ensure we copy the whole app4-dashboard/api to maintain import paths
COPY --chown=10001:10001 app4-dashboard/api/ /app/app4-dashboard/api/

USER 10001:10001

CMD ["uvicorn", "app4-dashboard.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
