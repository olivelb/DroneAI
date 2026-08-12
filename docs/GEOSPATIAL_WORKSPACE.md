# Geospatial workspace and rerunnable AI analyses

## Scope

The Results workspace is the operational GIS surface for one DroneAI mission.
It combines windowed COG rendering, viewport-scoped vectors, rerunnable AI
campaigns, database search, measurements and collaborative manual features.
The browser never downloads the complete orthomosaic to display or zoom it.

## AI analysis lifecycle

The mission's initial AI result is produced by the bounded `detection` stage
Job in qualified Kubernetes mode. That executor streams the raster, performs
SAM3/YOLO inference and publishes immutable JSON/GeoJSON as the fifth stage
artifact.

The API below is a separate post-publication campaign facility. It retains the
Kafka processing/IA worker implementation for rerunning alternative models or
prompts against an existing COG. A stage-Job-only deployment must provision
those compatibility workers before enabling these campaign controls; the
mission monitor must not present their state as a sixth DAG stage.

`POST /maps/{vol_id}/analyses` creates an immutable analysis identity and an
outbox event in the same PostgreSQL transaction. The existing processing and
GPU inference workers then execute the campaign:

1. the processing worker opens the mission COG and writes overlapping JPEG
   inference tiles under
   `missions/{vol_id}/analyses/{run_id}/tiles/`;
2. every tile is journaled in `ai_analysis_tiles` before its deterministic
   Kafka event is sent;
3. the IA worker runs YOLO OBB or SAM 3 and preserves `analysis_run_id` in the
   response event;
4. the processing worker writes each tile response as a verified GeoJSON
   object before marking the receipt complete;
5. after every receipt is present, detections are deduplicated across overlap
   areas and a verified final `detections.geojson` is published;
6. when `persist_results=true`, the final deduplicated objects are also rebuilt
   in the indexed `map_features` PostGIS table.

The `persist_results=false` option does not write detection entities to
PostgreSQL. Only campaign state and recovery receipts remain in the database;
vectors stay in tile-scoped and final GeoJSON objects. The map API selects only
object-store tiles intersecting the requested WGS84 viewport.

### Recovery semantics

- API requests use the transactional outbox.
- Tile and result keys are deterministic and uploads are size/SHA-256 verified.
- A unique `(analysis_run_id, tile_index)` receipt makes replay idempotent.
- A stale run with all receipts is finalized again.
- Missing stale tiles are republished in bounded batches.
- A run that failed before journaling its first tile is re-tiled.
- Final PostGIS publication is a replace-in-transaction operation, so a
  finalizer crash cannot leave duplicate features.
- Operators can explicitly retry failed runs and cancel active runs from the
  dashboard. Cancelled runs are retained for audit and are not implicitly
  restarted.

## Map and feature API

Read routes require `viewer`; mutations require `operator`.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/maps/{vol_id}/analyses` | campaign list and progress |
| `POST` | `/maps/{vol_id}/analyses` | queue a COG analysis |
| `POST` | `/maps/{vol_id}/analyses/{run_id}/retry` | retry a failed campaign |
| `POST` | `/maps/{vol_id}/analyses/{run_id}/cancel` | cancel an active campaign |
| `GET` | `/maps/{vol_id}/analyses/{run_id}/vectors.geojson` | viewport vectors for either persistence mode |
| `GET` | `/maps/{vol_id}/search` | attribute/spatial search with aggregate result bounds |
| `GET` | `/maps/{vol_id}/export/raster/{layer}` | stream the orthomosaic or height map as COG/GeoTIFF |
| `GET` | `/maps/{vol_id}/export/vectors` | create a GeoPackage or GeoJSON export by source/campaign and CRS |
| `POST` | `/maps/{vol_id}/features` | create a manual WGS84 feature |
| `PATCH` | `/maps/{vol_id}/features/{feature_id}` | optimistic update using `version` |
| `DELETE` | `/maps/{vol_id}/features/{feature_id}` | audited tombstone of a manual or persisted-AI feature |
| `POST` | `/maps/{vol_id}/features/bulk` | review, unreview, tombstone or restore selected features |
| `GET` | `/maps/{vol_id}/features/{feature_id}/audit` | append-only correction history |
| `GET/POST` | `/maps/{vol_id}/styles/{layer}` | list/create named raster recipes |
| `PATCH` | `/maps/{vol_id}/styles/{layer}/{style_id}` | versioned named-style update |
| `POST` | `/maps/{vol_id}/gcps/import` | import a surveyed GCP set and propose visible/nearby photos |
| `GET` | `/maps/{vol_id}/gcps` | list GCP sets as a WGS84 vector layer |
| `PATCH` | `/maps/{vol_id}/gcps/points/{point_id}` | edit coordinates, role and accuracy with optimistic locking |
| `PATCH` | `/maps/{vol_id}/gcps/observations/{observation_id}` | mark or skip one source-photo observation |
| `POST` | `/maps/{vol_id}/gcps/{set_id}/candidates/refresh` | add camera-visible photos, then EXIF-nearby fallbacks, without replacing operator decisions |
| `POST` | `/maps/{vol_id}/gcps/{set_id}/bundle` | validate and publish immutable reconstruction inputs |
| `GET` | `/maps/{vol_id}/gcps/{set_id}/audit` | read the append-only GCP operator history |

Accepted manual geometries are Point, LineString, Polygon and their Multi
variants in EPSG:4326. Coordinates, vertex count, color, tag count and text
lengths are validated server-side. Persisted AI features can be corrected
without changing the immutable source campaign. Their provenance remains
attached and every correction records the operator plus before/after state.
Legacy detections that were not persisted remain read-only.

Search filters include free text, source, campaign, class, confidence, review
state, active/withdrawn state and an optional WGS84 bounding box. The response
includes `bounds`, which the
dashboard uses to frame all returned zones. Persisted AI and manual features
use the PostGIS GiST index. Legacy pipeline detections remain searchable by
class and spatial extent.

## Dashboard use

Facade missions expose their RGB and local-depth products in Results, but they
are not web maps. The layer panel labels them as local, does not invent an EPSG
code or Web Mercator position, and keeps aerial detections/analysis campaigns
out of the process. Use the COG download together with `facade_frame.json` for
QGIS/CAD workflows that support an explicit local engineering frame. A
GeoJSON/PostGIS export is meaningful only after a separate, documented
registration to a map CRS.

The workspace is responsive and contains five panels:

- **Couches** controls RGB/single-band composition, COG-wide percentile or
  fixed min/max stretch, opacity, DEM palettes, named recipes, legacy
  detections, manual objects and independent AI campaign visibility.
- **IA** configures name, description, color, tags, backend, model/prompt,
  classes, confidence, tile size and PostGIS persistence. It also shows phase,
  tile progress, errors, retry/cancel controls and final download.
- **Objets** applies database/review filters, lists matches, selects persisted
  objects in bulk, marks them reviewed/unreviewed and performs reversible
  removal/restoration.
- **Export** streams the orthomosaic or height raster as COG/GeoTIFF and exports
  all vectors, AI/legacy subsets or manual annotations as GeoPackage or
  GeoJSON. GeoPackage is the recommended QGIS 4+ interchange format because it
  preserves mixed geometries, long field names and UTF-8 attributes in one
  file. One CRS control applies consistently to AI detections, legacy vectors
  and manual annotations: the orthomosaic CRS is the default, WGS84 is
  available explicitly, and an operator may enter a validated `EPSG:<code>`.
  The backend reprojects the WGS84 database geometries while streaming them
  into the GeoPackage and records the selected EPSG definition and extent in
  its metadata. If the raster CRS is absent or cannot be resolved to EPSG, the
  default falls back to EPSG:4326 and the response declares that fallback.
  GeoJSON always uses EPSG:4326 as required by RFC 7946. Every vector format
  keeps the source, campaign, confidence, name, description, tags, color and
  full source property JSON.
- **GCP** imports surveyed control, edits WGS84 coordinates and accuracy,
  displays the control network as vectors and opens the original photographs
  for precise native-pixel marking.

`GET /maps/{vol_id}/export/vectors` accepts:

- `format=gpkg|geojson`;
- `scope=all|manual|ai|legacy`;
- optional comma-separated `run_ids`;
- `crs=raster|EPSG:<code>`.

For GeoPackage, `crs=raster` is the default. A non-WGS84 CRS requested with
GeoJSON is rejected rather than producing a non-standard or misleading file.
Downloaded names include the effective EPSG code, and the response exposes it
through `X-Coordinate-Reference-System`.

Chromium browsers use the native save-file picker and stream large rasters
directly to the selected destination without buffering the complete GeoTIFF in
browser memory. Browsers without the File System Access API fall back to their
normal download destination. The server never accepts a client-provided
filesystem path.

The map toolbar supports navigation, point/line/polygon creation and
distance/area measurement. A measure can remain temporary or be saved as a
normal tagged feature. Manual and persisted-AI objects can be selected,
corrected, reviewed or withdrawn. Withdrawal creates a tombstone and audit
event rather than deleting the row; bulk operations follow the same rule.
Concurrent edits are protected by a version check and return HTTP 409 rather
than overwriting another operator's change. GeoJSON and GeoPackage exports
omit tombstoned rows.

## Ground-control workflow

The GCP workspace accepts CSV, TSV, whitespace-delimited TXT/XYZ, GeoJSON, KML,
Metashape marker XML and ODM `gcp_list.txt`. Delimited tables recognise common
`id/name/label`, `x/easting/lon`, `y/northing/lat`, `z/altitude`, role and
accuracy columns. Leica and Trimble presets are available; an operator can map
custom identifier/X/Y/Z column names without altering the source file. XML
DOCTYPE/entity declarations are rejected. A projected input must declare its
CRS (for example `EPSG:2154`); KML is WGS84, while GeoJSON, Metashape XML and
ODM may declare a CRS. Map and manual coordinate controls use WGS84
(`EPSG:4326`) while original survey coordinates remain available for
calculation and provenance.

The normal operator sequence is:

1. import a named set, its CRS, default adjustment/checkpoint role and survey
   accuracies;
2. run reconstruction preflight so `geo_data.txt` and its CRS are published;
3. refresh candidates: before a registered sparse model exists, photos are
   ranked by metric EXIF distance; after geo-alignment, the worker publishes a
   portable camera index and the API ranks only frames where the surveyed point
   projects inside the calibrated image, using EXIF distance only to fill any
   remaining slots. Existing marks and explicit skips remain untouched;
4. select each point, correct coordinates if required, then mark the exact
   target in every useful original photo or skip photos where it is not
   visible;
5. keep points used in bundle adjustment as `adjustment`, reserve independent
   accuracy controls as `checkpoint`, and set unusable points to `disabled`;
6. validate the set for reconstruction.

The photo editor preserves original image dimensions, supports 5–400% zoom, a
high-magnification loupe and keyboard nudging (1 px, 0.1 px with Shift, 10 px
with Alt). A camera projection seeds the marker when available, but the
operator remains the authority on visibility and exact placement. Native pixel
coordinates are checked again by the API against dimensions read incrementally
from the original object; non-finite and out-of-frame annotations are rejected.
Point and observation mutations carry a version and reject concurrent
overwrites.

ODM and Metashape imports apply the same trust boundary. Their projection
coordinates must be finite and non-negative. When a published camera index
provides the original image dimensions, the import checks the pixel bounds
before accepting the observation as marked. Without those dimensions, the
imported pixel is retained as a `candidate` seed and requires explicit operator
confirmation in the photo editor. Bundle materialisation independently repeats
the finite and bounds checks for every marked observation and refuses historical
or malformed marks that have no validated image dimensions or available source
image.

Imports, coordinate/role edits, photo marks/skips, candidate refreshes and
bundle materialisations are recorded in `gcp_audit_events` in the same database
transaction as their mutation. The dashboard exposes human-readable event
labels and retains expandable before/after JSON. PostgreSQL prevents direct
updates or deletes of audit rows; parent mission/set lifecycle deletion remains
possible through its declared cascade.

Validation requires at least three adjustment points and at least two marked
photos for every active adjustment or checkpoint. Disabled points and
checkpoints are excluded from bundle adjustment. Checkpoints remain in the
accuracy sidecar so downstream quality evaluation can report independent
residuals. A set without checkpoints is labelled
`adjustment-only-unverified`, not independently accurate.

`POST /maps/{vol_id}/gcps/{set_id}/bundle` does not launch computation. It
returns descriptors for an ODM-compatible `gcp_list.txt` and an accuracy/role
CSV, both published under content-addressed S3 keys. Tenant missions use schema
v2 and organization-scoped CAS; historical `legacy-unassigned` missions retain
schema v1 global CAS. Supply that exact response as `parameters.gcp_bundle`
when creating the new reconstruction stage run:

```json
{
  "parameters": {
    "gcp_bundle": {
      "schema_version": 2,
      "organization_id": "acme-survey",
      "set_id": "<set UUID>",
      "source_sha256": "<source SHA-256>",
      "gcp_list": {"key": "organizations/acme-survey/blobs/sha256/ab/<SHA-256>", "size": 123, "sha256": "<SHA-256>"},
      "accuracy_csv": {"key": "organizations/acme-survey/blobs/sha256/cd/<SHA-256>", "size": 456, "sha256": "<SHA-256>"},
      "quality": {
        "adjustment_points": 6,
        "checkpoint_points": 3,
        "marked_observations": 24,
        "verification": "independent-checkpoints"
      }
    }
  },
  "upstream_artifact_ids": {}
}
```

Send this body to `POST /missions/{vol_id}/stages/reconstruction/runs` with a
unique `Idempotency-Key`. The reconstruction executor downloads both blobs,
verifies size and SHA-256 before use, and never silently falls back to mutable
dataset files when a bundle was requested. Downstream stages must then be
rerun from the newly published reconstruction artifact according to the
normal immutable DAG contract.

### Maintenance boundaries

`ResultsViewer.tsx` coordinates mission selection and shared map state only.
The layers, AI campaign, search, export and feature-editing interfaces live in
focused components under `components/geospatial/`; CRS selection and vector
export cards are separate from download orchestration, and shared defaults and
geometry helpers live in `workspace-config.ts`. GCP and analysis lifecycle
state live in dedicated controller hooks, while manual coordinate entry is a
separate editor component. On the API side,
`routers/maps.py` only composes the raster, export, campaign, feature-mutation
and named-style routers. Request models and the framework-neutral
GeoJSON/GeoPackage writer
are kept outside those routers; CRS resolution and lazy reprojection live in a
separate shared module.

These boundaries are checked in `tests/test_modular_boundaries.py`. CI also
runs Ruff's C90 complexity rules against the geospatial API and processing
orchestration services.

## Deployment

Run the database migration before rolling out API and workers:

```bash
helm upgrade --install drone-ai charts/drone-ai \
  --namespace drone-ai --create-namespace \
  -f charts/drone-ai/values-production.example.yaml
```

The Helm migration job runs `alembic upgrade head`; API and worker init
containers wait for the configured schema head. No additional Kafka topic is required:
campaign events extend the existing versioned orthomosaic, image-tile,
tile-detection and control contracts with `analysis_run_id`.

GCP deployment requires migrations `0019` through `0021`, API write access to
the content-addressed object prefix and reconstruction-worker read/write access
to the mission prefix for `camera_projection_index.json`. Apply `alembic
upgrade head` before rolling out the frontend, API and reconstruction worker.
Existing missions remain valid: candidate selection falls back to EXIF when no
camera index exists, and dataset GCP files remain in use unless an immutable
`gcp_bundle` is explicitly supplied to a new reconstruction stage run.
