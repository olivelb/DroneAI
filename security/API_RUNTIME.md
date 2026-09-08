# API runtime supply-chain audit (2026-09-08)

The API image keeps Python 3.12 and its pinned Debian base. It removes unused
`bsdutils`, `login`, `mount`, `util-linux`, `perl-base`, `gzip`, and `ncurses-bin` through apt, followed
by autoremove. These packages are Essential for a general-purpose Debian system;
the explicit removal is limited to this immutable service image, never a host.
Installed-package metadata remains intact for Trivy and SBOM tooling. The build
checks that the removed gzip and infocmp executables are absent.

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
- First reduced runtime scan: **14 HIGH + 0 CRITICAL**, all unfixed.
- Removing gzip and ncurses-bin: **12 HIGH + 0 CRITICAL**, all unfixed.
- Actual Uvicorn `/live` and `/openapi.json` passed in a non-root read-only
  container with no external network; Rasterio imports and TLS trust passed.

Residual findings by package: libuuid1 (4), libsqlite3-0 (2), libacl1 (1),
libncursesw6 (1), libsystemd0 (1), libtinfo6 (1), libudev1 (1), ncurses-base (1). Counts are a dated scanner result, not an exploitability
assessment. The unchanged promotion policy still rejects these findings unless
they are fixed, removed, or individually justified in the waiver registry.
No waiver was created and this change does not qualify production promotion.

The PR's PostgreSQL/Kafka/S3 composition and supply-chain artifacts provide the
integration evidence for its exact candidate. Revert the Dockerfile change to
restore the prior runtime; no application schema or API contract changes here.

## SQLite correction (2026-09-08)

`libsqlite3-0=3.53.4-2` is fetched with APT signature verification from the
[Debian snapshot of 2026-09-08](https://snapshot.debian.org/archive/debian/20260908T000000Z/).
Only the download stage sees sid. The final runtime keeps its trixie sources,
glibc and package database; dpkg installs the exact package and apt checks the
dependency graph. The amd64 package SHA256 is
`bbf13c9326764d05e37e6590debdaa6f33af6bd822e3ca1204789d9d1dc23b11`.

Debian identifies this package as fixed for
[CVE-2026-11822](https://security-tracker.debian.org/tracker/CVE-2026-11822) and
[CVE-2026-11824](https://security-tracker.debian.org/tracker/CVE-2026-11824).
The image build now verifies Python's loaded SQLite version, an FTS5 search and
integrity check, every Rasterio-bundled SQLite's version/FTS5 compile option,
and an actual GDAL GeoPackage pixel/CRS roundtrip. The promotion script repeats
these checks as UID 10001 using repository-owned code on the immutable target
digest before applying any waiver or signing. The old SQLite package fails
this check. Current local runtime qualification is linux/amd64.

Trivy's trixie advisory can still report both CVEs on the corrected sid package;
raw counts must not be described as unresolved exploitability or hidden. No
SQLite waiver is introduced in this phase. Update this snapshot/version when a
qualified stable fix is available, and rerun the image checks. Revert this
Dockerfile and verifier change together to restore the prior package policy.

## ACL correction and consumers (2026-09-08)

The same signed snapshot now provides `libacl1=2.4.0-1`, which Debian marks
fixed for [CVE-2026-54369](https://security-tracker.debian.org/tracker/CVE-2026-54369).
It is installed together with `tar=1.35+dfsg-5`: that package's Debian changelog
records the fix for [the ACL symbol collision, bug 1141146](https://bugs.debian.org/1141146).
The amd64 SHA256 values are respectively
`e9da0e00387e31c1709b70497f1eda91389c962c3940e6d233d4c57f5ea6f635` and
`c1c24b21c27006e49d691d41c847ad09fbbd6516626a942f69e59c15fbecc81e`.

The image checker now tests fd-based extended ACL writes, directory-relative
reads, explicit rejection of symlink following, and real ACL preservation
through `cp`, in-place `sed` and `tar` archive/extract. This checks the loaded
ABI and consumers, not only package version strings. It runs as the service
UID, also on the exact digest before promotion. An image with the previous
ACL package is rejected. No ACL waiver is added in this phase.

The new `_at` APIs enable safer callers; legacy pathname APIs can still follow
symlinks by design. The package upgrade does not turn arbitrary privileged
legacy callers into safe code. The API runs as UID 10001 and qualification drops
all capabilities. Any future privileged ACL consumer needs its own path/fd audit.
Rollback must restore ACL, tar and the corresponding verifier together.
