# Test-only MinIO images: official upstream sources, immutable revisions.
FROM golang:1.26.8-bookworm@sha256:a688600ca24f8a4d3ca77f95b0dd40704a9fc787c826660eb7ba0b641b8b175d AS builder
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
WORKDIR /src

FROM builder AS server-build
RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 7aac2a2c5b7c882e68c1ce017d8256be2feea27f \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 7aac2a2c5b7c882e68c1ce017d8256be2feea27f \
    && go build -mod=readonly -trimpath -o /out/minio .

FROM builder AS client-build
RUN git init . \
    && git remote add origin https://github.com/minio/mc.git \
    && git fetch --depth=1 origin 77f82e18b5401a65958f1619df6ebb994634bd88 \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 77f82e18b5401a65958f1619df6ebb994634bd88 \
    && go build -mod=readonly -trimpath -o /out/mc .

FROM debian:bookworm-slim@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251 AS runtime
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

FROM runtime AS server
LABEL org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="7aac2a2c5b7c882e68c1ce017d8256be2feea27f" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=server-build /out/minio /usr/local/bin/minio
COPY --from=server-build /src/LICENSE /usr/share/doc/minio/LICENSE
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]

FROM runtime AS client
LABEL org.opencontainers.image.source="https://github.com/minio/mc" \
      org.opencontainers.image.revision="77f82e18b5401a65958f1619df6ebb994634bd88" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
COPY --from=client-build /out/mc /usr/local/bin/mc
COPY --from=client-build /src/LICENSE /usr/share/doc/mc/LICENSE
ENTRYPOINT ["mc"]
