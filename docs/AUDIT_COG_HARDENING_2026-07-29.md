# Audit hardening and COG delivery

Date: 2026-07-29

This note records the implementation that follows the post-PR #9 audit. Items
that require unavailable independent survey data (GCP/checkpoints) are not
claimed as validated.

## Corrected contracts

- Native DroneGS `checkpoint_saved.path` is accepted by the Python adapter;
  `checkpoint` remains a compatibility fallback.
- The required orthomosaic is converted, uploaded with SHA-256 metadata and
  verified by S3 `HEAD` before `DONE` or the next-stage event.
- S3 directory downloads reject object keys whose resolved local path escapes
  the requested destination.
- Partitioned DroneGS V1 cells retain the profile seed instead of silently
  deriving a non-V1 seed.

## Durable processing

Migration `0003` adds:

- `processed_tiles`, unique on `(vol_id, tile_index)`;
- mission tiling metadata and aggregation state;
- an aggregation completion timestamp;
- `outbox_events.dead_at`.

Every AI response creates a tile receipt even when its detection array is
empty. A mission row lock serializes completion, and only the transaction that
changes the mission to `finalizing` publishes the vector product. A periodic
recovery pass retries complete missions left in `collecting`/`failed`, and
reclaims stale `finalizing` rows after a crashed worker.

Outbox records enter `dead` at the configured retry limit. Administrators can
inspect and replay them:

```text
GET  /operations/outbox/dead
POST /operations/outbox/{id}/replay
```

The original failure reason remains stored until a replay succeeds.

## Raster delivery contract

App1 publishes:

```text
missions/<id>/orthomosaic.tif
missions/<id>/orthomosaic.tif.cog.json
missions/<id>/orthomosaic.preview.webp
missions/<id>/orthomosaic.height.tif
missions/<id>/orthomosaic.height.tif.cog.json
missions/<id>/orthomosaic.height.preview.webp
```

GeoTIFF products use:

- COG driver;
- 512-pixel internal blocks by default;
- DEFLATE compression;
- `BIGTIFF=IF_SAFER`;
- average-resampled internal overviews;
- atomic replacement after validation.

The WebP preview is bounded to 2,048 pixels by default. Generic image previews
reject sources over 64 MiB and Pillow rejects images over 80 million pixels.
GeoTIFF previews never read the full raster.

## Map API

```text
GET /maps/{vol_id}/metadata/{ortho|depth}
GET /maps/{vol_id}/tiles/{ortho|depth}/{z}/{x}/{y}.png
GET /maps/{vol_id}/vectors.geojson?bbox=west,south,east,north
GET /maps/{vol_id}/export/raster/{ortho|depth}?format=cog|geotiff
GET /maps/{vol_id}/export/vectors?format=gpkg|geojson&scope=...&crs=...
```

Raster tiles are read through COG HTTP range requests and reprojected with a
`WarpedVRT` into the exact EPSG:3857 tile grid. The source mission CRS remains
the selected engineering CRS (for example an RGF93 CC zone); Web Mercator is
only the visualization grid.

## Vector AI delivery

The processing worker no longer creates a second full-size annotated raster.
Detection/segmentation polygons are transformed from source pixels through the
orthomosaic affine transform and mission CRS into WGS84. They are stored in
the PostGIS `geometry(Polygon,4326)` column and exported to:

```text
missions/<id>/detections.geojson
```

The dashboard queries only the visible bounding box and overlays returned
GeoJSON on Leaflet. The spatial GiST index handles polygon intersection;
longitude/latitude centers remain a fallback for point-only detections.
QGIS delivery is generated on demand: GeoJSON remains WGS84, while GeoPackage
reprojects detections and manual annotations to the orthomosaic CRS by default
or to an explicitly selected EPSG code. The exported GeoPackage records that
CRS in both its spatial-reference and geometry metadata.

## Alignment quality gate

In addition to registered image ratio and non-empty points, the fast mapping
path now measures:

- mean and median point reprojection error;
- median track length.

Production defaults reject a mean reprojection error above 2 px or a median
track length below 3. Both thresholds are visible and editable in Mission
Studio. Independent absolute accuracy still requires GCP/checkpoint evidence.

## API and security

- Infrastructure/application failures use 4xx/5xx responses instead of
  success-shaped HTTP 200 payloads.
- Production WebSockets no longer accept credentials in query strings.
- Cookie-authenticated mutations require a configured trusted `Origin`.
- Ceres/COLMAP CUDA builds cover Turing, Ampere, Ada, Hopper and Blackwell by
  default and accept an overridable `CUDA_ARCHITECTURES` build argument.

## Deployment

The API image now contains `alembic.ini` and migration sources. Helm creates a
revisioned migration Job that executes:

```bash
alembic upgrade head
```

Database-dependent workloads have an init container that waits until
`alembic current` reports the target head. Pods use the runtime default
seccomp profile, disable privilege escalation and drop Linux capabilities.

CI now adds:

- Helm lint;
- production Helm rendering;
- production npm dependency audit.

## Validation completed

- targeted Python tests for COG conversion/rendering, vector projection,
  zero-detection tile accounting, S3 verification/traversal, outbox terminal
  state, sparse quality and API security;
- Ruff;
- Python compileall;
- frontend ESLint, TypeScript and production build;
- `npm audit --omit=dev --audit-level=high`;
- Helm lint and production render;
- `git diff --check`.

GPU execution, real S3 range behavior, the PostGIS migration round-trip and
end-to-end ALBAGNAC/SAVERES runs remain release-environment gates. GCP accuracy
is explicitly deferred until independent control data exists.
