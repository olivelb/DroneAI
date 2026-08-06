# DroneAI production readiness

Date: 2026-07-29

## Supported deployment boundary

The repository now provides an authenticated, role-separated **single-tenant**
production baseline. It is suitable behind a TLS ingress for one organization.
It is not yet a public multi-tenant SaaS: that boundary additionally requires
OIDC, tenant ownership columns and object-prefix isolation.

## Required production configuration

Deploy with `charts/drone-ai/values-production.example.yaml` as a reviewed
overlay. Before installation, create:

- the storage Secret with `s3-access-key`, `s3-secret-key` and `database-url`;
- the API auth Secret with `api-keys.json` and a distinct random
  `session-secret` of at least 32 characters;
- the ingress TLS Secret.

`api-keys.json` is a JSON array:

```json
[
  {
    "key": "a-random-secret-of-at-least-32-bytes",
    "subject": "droneai-operations",
    "role": "admin"
  }
]
```

Create the Kubernetes Secret from local files that are kept outside the
repository:

```bash
openssl rand -base64 48 > session-secret
kubectl -n drone-ai create secret generic drone-ai-api-auth \
  --from-file=api-keys.json=./api-keys.json \
  --from-file=session-secret=./session-secret
```

Do not pass either value as a command-line literal or commit these files.

Roles are cumulative:

- `viewer`: status, parameters, datasets and artifacts;
- `operator`: viewer plus mission start/cancel/resume and upload;
- `admin`: operator plus mission/dataset deletion.

Clients use `Authorization: Bearer <key>` or `X-API-Key`. Production WebSocket
clients use the `droneai_api_key` secure cookie; query-string tokens are
accepted only in development.
Mission Studio prompts for the provisioned key and exchanges it through
`POST /auth/session` for an eight-hour HttpOnly, Secure, SameSite=Lax cookie.
The cookie contains a signed expiry-bearing session token, not the raw key.
The raw key is not retained in JavaScript storage, embedded in the frontend
bundle or placed in the WebSocket URL. `DELETE /auth/session` signs out.

Set `dashboardFrontend.apiUrl` to the public HTTPS API origin. The value is
read by the Next.js server at runtime, so host changes do not require an image
rebuild. `CORS_ORIGINS` must contain the corresponding frontend origin.

Production startup fails when:

- `CORS_ORIGINS` contains `*`;
- authentication is disabled or no API key registry is present;
- S3/database variables are missing or use known local defaults.

Cookie-authenticated mutations also require a configured trusted `Origin`.

## Upload policy

The API accepts aerial images plus DJI/GNSS sidecars and enforces:

- `DRONEAI_UPLOAD_MAX_FILES` (default 2,500);
- `DRONEAI_UPLOAD_MAX_FILE_BYTES` (default 2 GiB);
- `DRONEAI_UPLOAD_MAX_BATCH_BYTES` (default 50 GiB);
- a fixed extension allow-list.

Retention and lifecycle rules remain the responsibility of the selected S3
service and must be configured before public ingestion.

## Geodetic product contract

Horizontal output uses one recorded metric CRS for the entire mission. Small
metropolitan French missions use the appropriate RGF93 CC9 zone; wide missions
fall back to Lambert-93 and other countries to UTM unless an EPSG code is
selected explicitly.

DJI MRK `Ellh` remains **ellipsoidal**. The CRS sidecar records the source and
vertical uncertainty and states that no orthometric conversion was applied.
NGF-IGN69 publication requires an explicit, versioned RAF20/Circé grid
transformation and must not be inferred from EXIF.

Raster products are tiled COGs with internal overviews, a bounded WebP preview
and a metadata sidecar. The map API reprojects each requested tile precisely
to Web Mercator without loading the complete orthomosaic. AI segmentations and
detections are stored as WGS84/PostGIS vectors and queried by viewport; no
second full-size annotated raster is generated. On download, RFC 7946 GeoJSON
remains EPSG:4326 while GeoPackage defaults to the orthomosaic EPSG and can be
reprojected to WGS84 or an explicitly validated EPSG code. Raster and vector
downloads are streamed, and a missing/unresolvable raster CRS produces an
explicit WGS84 fallback header.

The facade process is a different product contract. It writes
`facade_orthophoto.tif`, `facade_orthophoto.height.tif`,
`facade_frame.json` and `facade_selection_report.json` in a local wall frame
with no CRS. It never publishes `images-ortho`, so TILER and IA are not
required for terminal success. Releases must verify that the RGB/depth COG
metadata remains `coordinate_space=local`, that the manifest records
`FACADE_HD_V1`, and that no absolute RTK, GCP or gravity option
can leak into the facade frame.

## Distributed durability contract

- The required orthomosaic is uploaded with SHA-256 metadata and verified by
  `HEAD` before `DONE` or the downstream event can be published.
- Every AI tile response has a unique database receipt, including responses
  with zero detections.
- Aggregation completion is locked in PostgreSQL and stale finalizations are
  recovered after a worker restart.
- Outbox events enter `dead` after their retry budget; administrators can list
  and explicitly replay them.
- The revisioned Helm migration job runs `alembic upgrade head`, while init
  containers prevent database-dependent services from starting on an old
  schema.

## Release gates

Required on every candidate:

1. Full Python suite in API/CUDA-capable test images.
2. Native DroneGS CPU and CUDA tests on a real GPU.
3. Frontend unit tests, lint, production build and Playwright mission journeys.
4. Helm lint with the production overlay.
5. One complete RTK preparation run and one non-RTK regression scene.
6. Immutable benchmark bundle with binary/dataset/artifact hashes.
7. Facade regression: inclusive exclusion audit, sparse-distribution metrics,
   local CRS-free raster metadata and terminal dashboard status.

The spatial-block implementation is ready, but its production PSNR/SSIM/LPIPS
thresholds remain a measured gate: use at least five complete ALBAGNAC and
SAVERES repetitions before creating `DRONEGS_PRODUCTION_PROFILE_V2`.

## CUDA 12.9.2 qualification status

The current CUDA 12.9.2 development and production runtime contracts were
executed locally on 2026-08-06 with an RTX 4070 Laptop GPU and driver 610.62.
All six native DroneGS suites passed and NVIDIA driver injection succeeded in
both production runtime images. The complete local record and its scope are in
[`benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md`](benchmarks/cuda-12.9.2-runtime-qualification-2026-08-06.md).

That run qualifies the pending audit-hardening working tree based on commit
`c89f31e`; it is not immutable release evidence. A release candidate must still
produce a successful, commit-scoped artifact from `dronegs-gpu-nightly.yml`
after the changes are committed.

## SAVERES RTK release evidence

The complete 1,066-image SAVERES preparation run registered every camera and
completed the cold path, including a 15.20 GB Windows-to-WSL copy and
undistortion, in about 41 minutes. The optional covariance-aware RTK pass added
25.4 seconds and reduced the 3D camera-prior residual median from 0.343 m to
0.106 m and P95 from 0.750 m to 0.210 m. Exact timings and diagnostics are
recorded in
`docs/benchmarks/saleres-alignment-rtk-2026-07-28.json`.

This validates the operational speed gate and the RTK integration path. It does
not replace an independent GCP/checkpoint accuracy report.

## SAVERES DroneGS release evidence

Five complete `DRONEGS_PRODUCTION_PROFILE_V1` runs, with seeds 42 through 46,
finished successfully on the 1,066-image RTK scene. Each run trained on 932
views, evaluated 134 held-out views, reached the configured 1.5-million
Gaussian cap and produced a distinct, hashed PLY.

The median end-to-end training time was 607.1 seconds (10 min 07 s), with a
616.5-second mean and a 603.8–655.5-second observed range. Median peak VRAM was
2,124 MiB on the RTX 4070 Laptop GPU. Held-out quality was highly repeatable:
mean PSNR 19.4122 dB (sample standard deviation 0.0075) and mean SSIM 0.49155
(sample standard deviation 0.00029).

The lightweight, reviewable record is
`docs/benchmarks/saleres-dronegs-production-v1-2026-07-28.json`. The complete
6,917,872,584-byte evidence archive remains outside Git at
`<benchmark-root>/saleres-dronegs-production-v1.tar.gz`;
its SHA-256 is
`5ed455a9f4a1f3cc628bec0d18f8fa15231490e2e86e13d5d7f186780cf9b7e2`.

This closes the repeated-run gate for SAVERES and production V1. It does not
promote the optional spatial-block V2 candidate: that requires equivalent
ALBAGNAC and SAVERES comparison runs and independently chosen acceptance
thresholds.

## Helenenschacht ultra-resolution negative evidence

The custom 30,000-step Helenenschacht run rendered a 5 mm/pixel COG but failed
the production SSIM threshold in force at the time (0,2763 measured versus
0,35 required), took 6404 seconds and retained 4,997 cm horizontal checkpoint
RMSE from the sparse alignment. Subsequent full-scene evidence moved the
current production threshold to 0,25 and separated threshold re-evaluation
from training compatibility, so this completed result can now be re-evaluated
without retraining. It still does not make 5 mm GSD a survey-accuracy claim or
replace the balanced production default.

The complete reviewable record is
`docs/benchmarks/helenenschacht-dronegs-ultra-5mm-2026-07-30.md`. It establishes
three independent release gates: projected raster GSD, held-out rendering
quality and independently measured survey accuracy. A threshold change must
be justified by scene evidence and recorded as acceptance policy; it does not
alter the immutable training contract or the measured survey error.
