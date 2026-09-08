# API runtime supply-chain audit (2026-09-08)

The API image keeps Python 3.12 and its pinned Debian base. It removes unused
`bsdutils`, `login`, `mount`, `util-linux`, and `perl-base` through apt, followed
by autoremove. These packages are Essential for a general-purpose Debian system;
the explicit removal is limited to this immutable service image, never a host.
Installed-package metadata remains intact for Trivy and SBOM tooling.

OpenSSL CLI remains required by the CA-certificate package. The legacy provider
is a dependency of `libssl3t64`; removing it directly breaks the dependency graph.
Python runtime libraries such as UUID and SQLite are preserved.

## Qualification

- `pip check`: passed during the image build.
- All 66 API modules imported as UID 10001 on a read-only root filesystem.
- OpenAPI exposes 75 paths; the default TLS context has trusted CA certificates.
- The CI image smoke test now imports every API module and checks TLS trust.
- Trivy 0.73.0 and CycloneDX SBOM generated from the actual final image.
- Reference CI run 34108173960: **51 HIGH + 3 CRITICAL**, all unfixed.
- Reduced runtime scan: **14 HIGH + 0 CRITICAL**, all unfixed.

Residual findings by package: libuuid1 (4), libsqlite3-0 (2), gzip (1), libacl1
(1), libncursesw6 (1), libsystemd0 (1), libtinfo6 (1), libudev1 (1), ncurses-base
(1), ncurses-bin (1). Counts are a dated scanner result, not an exploitability
assessment. The unchanged promotion policy still rejects these findings unless
they are fixed, removed, or individually justified in the waiver registry.
No waiver was created and this change does not qualify production promotion.

The PR's PostgreSQL/Kafka/S3 composition and supply-chain artifacts provide the
integration evidence for its exact candidate. Revert the Dockerfile change to
restore the prior runtime; no application schema or API contract changes here.
