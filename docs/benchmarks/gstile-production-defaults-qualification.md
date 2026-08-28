# GSTile production-default promotion and BIGZEN rollout

Date: 2026-08-28. Runtime `549153f2ea2854417f83397ab7d10d71f032fed1`,
merged in [PR #290](https://github.com/olivelb/DroneAI/pull/290) as
`2d0195090774d3e8620cdc53b038264711db8d14`. The
[production policy](../contracts/gstile-production-defaults-v1.md) defines the
defaults and explicit rollbacks. This record adds deployment evidence only.

## Acceptance and results

This is promotion of previously explicit, visually qualified settings, not a
new numerical or rasterization algorithm. The predeclared integration gate was
whole-bundle file/SHA equality between the old explicit profile and new defaults
on each runtime. No new statistical speedup is claimed from these single pairs.

- Red tests initially rejected the former defaults and bundle identity.
- 418 focused producer, real stage-adapter, startup and API tests passed.
- Complete CPU suite: 1,621 passed, 1 optional `plyfile` test skipped because the
  local test environment lacked that package; 37 GPU/integration tests deselected.
- `make static` passed. Frontend: 346 GSTile tests, scoped lint, typecheck and
  production Next build passed. Required PR CI gates passed, including service
  image imports and PostgreSQL/Kafka/S3 composition.
- Existing exact pack, cancellation, interruption and cleanup assertions remain;
  historical fixtures now explicitly select their old leaf-only/pack strategies.

The common source is synthetic SH3/directional opacity, **not** the full real
Saint-Etienne model: 1,048,576 records, SHA-256
`c2ce833ad2e8971055b45f8be82affc0683354192650a2659848bc459f779dbb`.
Profile: 65,536 leaves, 131,072 chunks, adaptive-moment proxies 16,384, 2 MiB
depth-spatial packs, two workers, 128 MiB pending reservation, timeout 16.

| Check | Old explicit | New default | Verdict |
|---|---|---|---|
| MSI WSL Python 3.12.3, NumPy 2.4.6, Zstandard 0.25.0 | 17.562 s | 17.038 s | All 63 files identical |
| BIGZEN Ryzen 9 5950X / 32 logical CPUs, same Python/NumPy/Zstd versions | 18.493 s | 18.284 s | All 63 files identical, UID 10001 |
| Installed BIGZEN CLI wrapper | Target qualified bundle | Wrapper-generated bundle | Binary `diff -rq` equal |

Durations include integration-check overhead, are not paired performance cohorts,
and must not be interpreted as another speed claim. MSI bundle ID is
`sha256:190c82ac43ce470269737fd70c35f8a6d0f669e9999b55e8a1edc8443b22d7eb`;
BIGZEN's is `sha256:83c2fca549fd2a003c3f93191fd4d6d510643577e2213822d6994181b960ee7a`.
They differ across hosts. This evidence establishes old/new equality **within**
each runtime, not cross-hardware bitwise reproducibility. BLAS thread counts and
arithmetic were not altered by the promotion.

## Target deployment

BIGZEN's dirty historical checkout `/home/olivier/droneAI` was not pulled,
reset, stashed or replaced. A Git archive of the exact runtime was extracted at
`/home/olivier/droneai-qualifications/gstile-production-defaults-20260828/source`.
Archive SHA-256: `f654a41507606a7ab821de98faa9306c4eedcfb50c8ed56a780e41174f281f5e`.

| Artifact | Immutable local Docker image ID |
|---|---|
| `droneai-frontend:549153f` | `sha256:8dd67a80dc07872e8b064e31c0a343c486c77bddfd4d04a7e2743537633385d0` |
| `drone-colmap:gstile-549153f-runtime` (CPU viewer-stage qualification only) | `sha256:89b929f59d3312d7bb219c077aa290018c6ad79ddfe9792c292ea842ccad42dc` |
| `droneai-gstile:549153f` (dedicated CLI entrypoint) | `sha256:4bb895dffa884e769f05c2e87a34d4eb653206ad82bc5d75a764f2810ecb2e75` |

The old base lacked Zstandard and prometheus-client. Their exact hashed lock
blocks were added without changing numerical dependencies. A real import of
`run_gaussian_viewer_stage` passed. The historical CUDA/CuPy and other old base
packages were **not** upgraded or requalified for reconstruction/training; do
not advertise this as a general COLMAP/GPU runtime release. No DroneAI Jobs were
active, no new full platform was installed, and OVH was excluded by the user.

The dedicated command is installed on BIGZEN as:

```bash
/home/olivier/.local/bin/droneai-build-gstiles SOURCE.ply NEW_OUTPUT_DIRECTORY
```

It mounts the source read-only, writes the new bundle as the calling user, uses
the pinned release tag and forwards explicitly present BLAS controls. Explicit
CLI rollback options still apply. The executable SHA-256 is
`389ed210b64febd7e9343385b57206ef6e8279b189f3190eb56eac7660c6ee25`.

The frontend was first smoke-tested on loopback port 3100, then switched to
3000. The operational drill completed: new container HTTP 200, restart of the
retained old container HTTP 200, then restoration of the new container HTTP 200.
Old container `droneai-frontend-gstile-0627d65` and stopped canary remain available.
No images, bundles, evidence or workspaces were deleted.

## Browser and serving checks

Chrome's actual React viewer loaded the unchanged real Saint-Etienne bundle
`sha256:33e9f9c1f20db32fed0b81119c708f2ecf64da56c95e217dbbb46a3df56dc432`.
Without a memory-profile query, DOM exposes 1,610,612,736 bytes (1,536 MiB).
Both candidate and final page settled at 356/356 nodes, approximately 7.4M splats,
merged rendering, no pending nodes and zero decode Worker fallbacks. No timing
cohort was automatically started and this is not a new operator visual approval.
Automated fullscreen activation was denied by Chrome (`not granted`); it was
not bypassed or counted as passing.

The real-bundle fixture API had disappeared after reboot. It was restored from
the retained server source, in a restartable, loopback-only container, with the
bundle on BIGZEN's I: mounted read-only. CORS permits only the two local frontend
origins. Mission metadata remains synthetic; missing parameter/WebSocket routes
produce harness-only console errors. This is not a full application API E2E.
The Windows SSH tunnel exposes local ports 3000/30080 (and the stopped canary's
3100 forward); it must be re-established after a connection or PC restart.

## Reproduction and retained evidence

Both hosts retain `/home/olivier/droneai-qualifications/gstile-production-defaults-20260828`.
The checked-in [MSI parity driver](gstile-production-defaults-parity.py) records
clean source commits, source/driver hashes, commands, stdout/stderr and inventories.
BIGZEN additionally retains `build-release.py`, `build-tiler-retry.py`,
`complete-runtime.py`, `Dockerfile.gstile-cli`, `target-parity.py`, `verify-cli.py`,
`rollout.py`, all source archives and complete output bundles.
`target-parity/verified-results.json` SHA-256 is
`b012b68daf14fd5eb73f36e4634e88b336c8586aa2e728e474dfdf9c1750bf22`.

Failed attempts remain separate: Docker rejected the local config digest used
directly as `FROM`; a verified local alias fixed the build. The first runtime
import identified missing prometheus-client. The first target test archive
omitted its PLY reader and failed before building; the reader was restored from
the exact baseline commit and a fresh retained container ran both complete arms.
None of these failures was silently treated as successful evidence.
