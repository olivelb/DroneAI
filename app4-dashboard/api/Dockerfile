FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS native-packages

# Signed immutable Debian snapshot; only this build stage can see sid.
# Expired metadata is allowed only for this dated archive; signatures stay required.
WORKDIR /packages
RUN printf '%s\n' \
      'deb [check-valid-until=no signed-by=/usr/share/keyrings/debian-archive-keyring.gpg] https://snapshot.debian.org/archive/debian/20260908T000000Z/ sid main' \
      > /tmp/native.list \
    && apt-get -o Dir::Etc::sourcelist=/tmp/native.list -o Dir::Etc::sourceparts=- update \
    && apt-get -o Dir::Etc::sourcelist=/tmp/native.list -o Dir::Etc::sourceparts=- \
      download libsqlite3-0=3.53.4-2

FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=native-packages /packages/*.deb /tmp/native-packages/
RUN dpkg -i /tmp/native-packages/*.deb \
    && apt-get check \
    && rm -rf /tmp/native-packages

# Remove unused OS utilities through apt, preserving an accurate SBOM.
# OpenSSL and its legacy provider remain required by CA certificates/libssl.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        libexpat1 libssl3t64 \
    && apt-get purge --yes --allow-remove-essential bsdutils login mount util-linux perl-base gzip ncurses-bin \
    && apt-get autoremove --yes \
    && test ! -e /usr/bin/gzip \
    && test ! -e /usr/bin/infocmp \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/api.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes \
    --requirement /tmp/requirements.txt \
    && pip check

RUN groupadd --gid 10001 droneai \
    && useradd --uid 10001 --gid 10001 --create-home droneai

COPY --chown=10001:10001 shared/ /app/shared/
COPY --chown=10001:10001 alembic.ini /app/alembic.ini
COPY --chown=10001:10001 alembic/ /app/alembic/
# Ensure we copy the whole app4-dashboard/api to maintain import paths
COPY --chown=10001:10001 app4-dashboard/api/ /app/app4-dashboard/api/

COPY scripts/ci/verify_api_runtime.py /opt/droneai/verify_api_runtime.py

USER 10001:10001
RUN python /opt/droneai/verify_api_runtime.py

CMD ["uvicorn", "app4-dashboard.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
