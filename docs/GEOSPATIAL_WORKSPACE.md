# Geospatial workspace and rerunnable AI analyses

## Scope

The Results workspace is the operational GIS surface for one DroneAI mission.
It combines windowed COG rendering, viewport-scoped vectors, rerunnable AI
campaigns, database search, measurements and collaborative manual features.
The browser never downloads the complete orthomosaic to display or zoom it.

## AI analysis lifecycle

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
| `DELETE` | `/maps/{vol_id}/features/{feature_id}` | delete a manual feature |

Accepted manual geometries are Point, LineString, Polygon and their Multi
variants in EPSG:4326. Coordinates, vertex count, color, tag count and text
lengths are validated server-side. AI features are read-only; changing model
results requires a new campaign and preserves provenance.

Search filters include free text, source, campaign, class, confidence and
optional WGS84 bounding box. The response includes `bounds`, which the
dashboard uses to frame all returned zones. Persisted AI and manual features
use the PostGIS GiST index. Legacy pipeline detections remain searchable by
class and spatial extent.

## Dashboard use

The workspace is responsive and contains four panels:

- **Couches** controls the COG/depth raster, opacity, legacy detections,
  manual objects and independent AI campaign visibility.
- **IA** configures name, description, color, tags, backend, model/prompt,
  classes, confidence, tile size and PostGIS persistence. It also shows phase,
  tile progress, errors, retry/cancel controls and final download.
- **Objets** applies database filters, lists matches and zooms to one result or
  to the aggregate result bounds.
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
normal tagged feature. Manual objects can be selected, renamed, described,
updated or removed. Concurrent edits are protected by a version check and
return HTTP 409 rather than overwriting another operator's change.

### Maintenance boundaries

`ResultsViewer.tsx` coordinates mission selection and shared map state only.
The layers, AI campaign, search, export and feature-editing interfaces live in
focused components under `components/geospatial/`; CRS selection and vector
export cards are separate from download orchestration, and shared defaults and
geometry helpers live in `workspace-config.ts`. On the API side,
`routers/maps.py` only composes the raster, export, campaign and feature
routers. Request models and the framework-neutral GeoJSON/GeoPackage writer
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
containers wait for revision `0004`. No additional Kafka topic is required:
campaign events extend the existing versioned orthomosaic, image-tile,
tile-detection and control contracts with `analysis_run_id`.
