# Explorer editing and raster style contract v1

## Scope

Migration `0015` makes operator corrections reversible and auditable without
changing immutable mission artifacts. It also stores named raster display
recipes independently from COG/GeoTIFF products. All routes remain scoped to
the authenticated mission owner; cross-owner administration uses the existing
explicit `owner_subject` boundary.

## Feature lifecycle

The map exposes distinct professional interaction modes: selection uses the
arrow (`V`), displacement uses the hand (`H`), and geometry creation and
measurement use a precision crosshair. Selection is toggled by clicking an
object, cleared by clicking empty map space or pressing Escape, and highlighted
without changing the stored style. The coordinate status control reports the
pointer in WGS84/EPSG:4326 with QGIS-style X/Y labels. Point, line and exterior
polygon vertices can also be entered numerically as longitude/latitude; polygon
ring closure is preserved by the geometry editor.

Manual and persisted-AI rows in `map_features` can be edited with optimistic
version checks. Removal is a tombstone (`deleted_at`, `deleted_by` and a
reason), never a physical delete. Tombstoned features are hidden from search,
map layers, analyses and GeoJSON/GeoPackage exports, but can be restored.

Every create, update, review, unreview, tombstone and restore appends a
`map_feature_audit_events` record containing the actor, time, reason and
before/after GeoJSON snapshots. Bulk actions lock all selected rows, reject a
missing or stale feature atomically and increment the version of each changed
row. Reapplying an already-satisfied lifecycle action is a no-op.

| Method | Route | Contract |
|---|---|---|
| `POST` | `/maps/{vol_id}/features` | Create and audit a manual feature |
| `PATCH` | `/maps/{vol_id}/features/{feature_id}` | Correct manual or persisted-AI geometry/attributes using `version` |
| `DELETE` | `/maps/{vol_id}/features/{feature_id}` | Tombstone one feature with an optional reason |
| `POST` | `/maps/{vol_id}/features/bulk` | `review`, `unreview`, `delete` or `restore` up to 500 UUIDs |
| `GET` | `/maps/{vol_id}/features/{feature_id}/audit` | Read the append-only audit trail |

Pipeline detections from the immutable detection artifact stay read-only.
Reprocessing an AI campaign tombstones the previous persisted rows before
publishing the replacement set, preserving their database history.

## Current artifact sources

Raster metadata, tiles and exports resolve the latest
`raster_product_workspace` using the organization-scoped
[Artifact Manifest v3](artifact-manifest-v3.md). A missing artifact returns 404;
root-level mission files are never a fallback. The frontend discovers ortho
and height layers from that artifact's metadata.

Vector selectors use `pipeline`, `manual` and `ai`; export `scope=all`
combines the three. The `pipeline` layer comes from the immutable
`detection_workspace` and is empty when it is absent. Historical SQL Detection
rows and the old `legacy` source selector are no longer supported.

Standalone analyses use bounded detection Stage Jobs pinned to the selected
raster artifact. Their output is a separate `detection_workspace` with an
organization-scoped Manifest v3; it never replaces the pipeline layer.
Non-persisted analyses read this immutable GeoJSON. When PostGIS indexing is
enabled, the features and artifact commit atomically. Manual annotations and
persisted analysis rows remain editable under the lifecycle rules above.
Retries allocate a new Stage Job on the original raster; cancellation does not
cancel the mission. Download through the analysis vectors API, not a raw CAS URL.

## Raster display recipes

The tile endpoint accepts one grayscale band or three unique RGB bands. A
recipe contains:

- `bands`: one index or three ordered RGB indexes;
- `display_ranges`: one range per selected band, or global metadata ranges;
- `stretch`: `global-percentile` (the COG-wide 2nd–98th percentiles) or
  explicit fixed min/max;
- `palette`: `gray`, `depth`, `terrain` or `viridis` for a single band;
- `opacity`: a browser-layer setting between 0.05 and 1.

COG metadata computes a bounded global range for every band. This avoids
per-tile normalization seams, especially on DSM/elevation products. The tile
renderer validates band availability and returns a client error for malformed
recipes.

Named recipes use `/maps/{vol_id}/styles/{layer}` and are stored in
`raster_layer_styles`. A style may reference the immutable mission artifact it
was designed for, has its own optimistic `version`, and can be marked as the
single default for a mission/layer. Editing a style never mutates the raster.

## Qualification boundary

The phase is covered by schema/lifecycle tests, direct raster rendering tests,
API route and modular-boundary tests, frontend serialization/unit checks,
strict Python typing/linting, frontend lint/build/E2E, and a PostGIS
`0014 -> 0015 -> 0014 -> 0015` migration round trip. It changes neither CUDA,
COLMAP nor DroneGS versions, so their long build/runtime jobs are intentionally
not required by path-based CI.
