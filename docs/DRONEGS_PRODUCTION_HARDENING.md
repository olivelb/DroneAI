# DroneGS production hardening plan

Date: 2026-07-28

This document records the critical review of the pre-COLMAP-4.1 / pre-dashboard
analysis against the current repository, the decisions taken, and the
remaining release work.

## Critical review

| Finding | Current audit | Decision |
|---|---|---|
| Production recipe contradicts the accepted benchmark | Valid. Pipeline defaults used `reference-absolute + bounded`, while the accepted dev.45 report used LichtFeld/reference-absolute rates, spatial bounds and structural FastGS. The report does **not** justify making the experimental dev.38 optimizer the production default. | Freeze `DRONEGS_PRODUCTION_PROFILE_V1` as `reference-absolute + spatial-bounds + fastgs`, 15k steps, factor 4, width 1600, cap 1.5M, progressive SH3, cooldown/photometric finish 1000/1000. |
| Duplicate manifest keys | Valid. `raster_profile`, `refine_every` and `grow_until_iteration` were duplicated; the final raster value described optimizer ancestry rather than the requested raster backend. | Keep one requested `raster_profile`, add one `effective_raster_profile`, remove duplicates, reject duplicate keys on load, validate and atomically promote the manifest. |
| Reuse does not identify the dataset | Valid. Reuse ignored the manifest fingerprint and the native fingerprint omitted intrinsics/poses. | Fingerprint full sparse binaries plus stable image path/size/content samples; native fallback also covers cameras, intrinsics, image IDs, qvec/tvec, points and image samples. Reuse compares it. |
| Reuse ignores binary and PLY identity | Valid. | Record current trainer binary SHA-256 and PLY SHA-256/size; reject reuse on any mismatch. |
| Checkpoint is local and deleted by worker cleanup | Valid. | Store it outside the workspace, sync each checkpoint event to S3, restore it before training, and retire it only after final promotion. |
| Mission cancellation does not stop DroneGS | Valid. | Poll cancellation independently of stdout; SIGTERM the process group, wait ten seconds, then SIGKILL. |
| Checkpoint lacks corruption protection | Valid. | Introduce format V3 with payload checksum, fixed-width new fields, file fsync, parent-directory fsync and rollback-safe publication. V1/V2 remain readable. |
| Native tests absent from CI | Valid. | Add CUDA-toolkit container jobs for CPU contract tests and a portable CUDA build; add an opt-in nightly self-hosted GPU suite. |
| Python accepts `mcmc` and `igs+` | Valid. | Restrict the Python/native contract to `mrnf`. |
| Experimental profile registry is duplicated | Valid. | Add a versioned native registry with status, version and validation scene; CLI validation and trainer lookup use it. |
| Benchmark evidence is local-only | Partly valid. The harness already hashes artifacts and performs repeated-run summaries, but lacked thermal/driver data, mean dispersion and a portable bundle. | Add GPU/driver/CUDA/temperature/power metadata, mean/stdev/95% CI and `--bundle`. Publishing the archive remains an explicit release action. |
| Modulo validation split is spatially correlated | Valid, but changing it inside V1 would destroy comparison continuity with dev.45. | Keep modulo-8 for immutable V1 parity. The custom/V2 path now supports a deterministic central spatial block and guard ring; production thresholds still require repeated-scene calibration. |
| Root licensing is ambiguous | Already corrected. | `THIRD_PARTY_NOTICES.md` states that the combined DroneGS CUDA binary is GPL-3.0-or-later and that the root MIT license covers only original DroneAI code. |
| Public API authentication, destructive endpoints, quotas and migrations | Valid. | Add API-key RBAC to every non-health route and WebSocket, admin-only deletion, operator-only mutation/upload, upload quotas, strict production CORS/secret validation and a production Helm overlay. This is a secure single-tenant baseline; public multi-tenant SaaS still requires external identity and ownership isolation. |

## Implemented release sequence

1. One immutable Python production profile feeds pipeline/API/dashboard/local
   runner defaults and the exact-command regression test.
2. Any dashboard expert edit marks the recipe `custom`; selecting production
   V1 reapplies the complete recipe.
3. The native command records profile ID, requested/effective raster backends
   and the externally computed dataset identity.
4. The adapter strictly loads the manifest, rejects duplicate keys, validates
   required fields, hashes the binary and PLY, runs the canary, then promotes.
5. Completed reuse validates dataset, binary, profile, canary and PLY.
6. Checkpoints survive handled exceptions, workspace cleanup and pod
   replacement through a durable local root plus S3 synchronization.
7. Cancellation reaches the subprocess without waiting for training to finish.
8. Native CPU/CUDA compilation and CI jobs protect the new contracts.
9. Custom canaries can use `spatial-block` with an explicit guard ring; the
   manifest and canary report training, held-out and ignored camera counts.
10. DJI `MRK` `Ellh` is preserved as ellipsoidal height in preflight and CRS
    sidecars. No undocumented NGF-IGN69 conversion is applied.
11. Production startup fails on wildcard CORS, missing API credentials, local
    MinIO defaults or a local database URL.
12. Corrected DJI MRK positions now replace EXIF pose priors with per-image ENU
    covariance. A single robust Ceres GPU pass is bounded independently and
    falls back to the verified GLOMAP/Caspar reconstruction.
13. The benchmark harness keeps its own logs outside the trainer's empty output
    directory and parses WSL `nvidia-smi` fields independently, including
    `[N/A]` power limits, so GPU/driver/temperature evidence is not discarded.
14. Mission Studio resolves the public API origin at runtime and exchanges an
    operator-entered API key for a bounded HttpOnly cookie; authenticated HTTP
    and WebSocket use the same session without embedding a secret in the
    frontend image.

## Next measured upgrades

### P1 — Spatial quality canary V2 baseline

Implemented:

1. Compute camera centers from COLMAP `qvec/tvec`.
2. Select the two dominant spatial axes, hold a deterministic central block,
   and exclude a configurable guard ring from training.
3. Preserve V1 modulo parity while recording the V2 split policy and all image
   counts in manifests and canary results.
4. Expose split and guard controls in mission schema, dashboard and local
   profiles.

Still measured, not guessed:

1. SAVERES V1 modulo parity is now measured over five complete seeds; preserve
   the exact record in
   `docs/benchmarks/saleres-dronegs-production-v1-2026-07-28.json`.
2. Run equivalent ALBAGNAC and spatial-block SAVERES repetitions.
3. Compare modulo parity with spatial generalization PSNR/SSIM/LPIPS.
4. Establish V2 thresholds before assigning a new immutable profile ID.

### P1 — Published benchmark evidence

Completed locally for SAVERES production V1:

1. Five runs were executed with `--bundle`.
2. The 6,917,872,584-byte tarball has SHA-256
   `5ed455a9f4a1f3cc628bec0d18f8fa15231490e2e86e13d5d7f186780cf9b7e2`.
3. The lightweight decision record contains the source revision, dirty-state
   disclosure, trainer binary/dataset/artifact hashes and aggregate metrics.

Still an explicit release action:

1. Publish the archive in the project release/object store.
2. Link that immutable external object from the release notes.

### Implemented — authenticated single-tenant deployment baseline

1. Every non-health HTTP route requires a viewer-or-higher API credential.
2. Mission mutation and uploads require `operator`; deletion requires `admin`.
3. WebSocket status access requires an authenticated token.
4. Upload extension, file count, file size and batch size quotas are enforced
   before S3 transfer.
5. Production rejects wildcard CORS, disabled auth and local credentials.
6. Helm can reference externally managed storage/auth Secrets and includes a
   safe production overlay.

### P0 only if exposed as a public multi-tenant SaaS

1. Integrate an OIDC identity provider and short-lived browser tokens.
2. Add tenant/owner columns, backfill migration and ownership filters to
   missions, datasets and object prefixes.
3. Add S3 lifecycle/retention policy managed by the target object store.
4. Exercise Alembic upgrades from every supported deployed release in CI.
5. Run distributed worker cancellation/retry chaos tests.
