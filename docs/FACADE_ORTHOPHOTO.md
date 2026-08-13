# Facade orthophotos

DroneAI can produce an orthorectified elevation of a building facade without
assigning a map CRS. Select **Façade** under **Processus de
production** in the reconstruction phase, or set `orthophoto_mode=facade`
through the API. The dashboard obtains the qualified values from the backend;
`shared/facade_process.py` is their single source of truth.

## Product contract

- output RGB COG: `facade_orthophoto.tif`;
- output depth COG: `facade_orthophoto.height.tif` (signed offset along the
  facade normal, not terrain elevation);
- local-frame report: `facade_frame.json`;
- image-selection audit: `facade_selection_report.json`;
- X points horizontally to the right, Y vertically upward, and Z out of the
  wall toward the cameras;
- the TIFF affine transform is expressed in local metres when scale is known,
  but its CRS is deliberately empty. It must not be displayed as a Web
  Mercator map.

The RGB product is rendered only from the trained Gaussian model. DroneAI does
not paste, feather or composite source photographs into the elevation; missing
coverage must be fixed in COLMAP/DroneGS rather than hidden during export.

The facade job stops after publishing these products. Aerial detection,
terrain processing and Web Mercator tile stages are not started.

## Why more than the frontal pass is useful

Only using images taken exactly normal to the wall gives visually clean
textures but weak depth triangulation. DroneAI therefore separates two needs:

1. coherent frontal and moderate oblique passes contribute features, camera
   poses and parallax to the reconstruction;
2. after bundle adjustment, only registered views within
   `facade_texture_max_incidence_deg` of the facade normal and carrying at
   least 20 sparse observations train the Gaussian texture. All registered
   passes remain available to constrain the geometry;
3. every unique image participates in SfM by default, so an incomplete
   attitude filter cannot silently remove an end of the elevation; incidence
   and sparse-observation gates are applied only after poses exist.

DroneGS is initialized from a coverage-oriented COLMAP gate: points observed
in at least two views and with at most 2 px reprojection error. This retains
thin borders and the foot of the wall that a three-view/1 px gate can remove.
For resident training, the two-view invariant is evaluated after restricting
each COLMAP track to the cameras assigned to that cell. The exported subset
report records the rejected points and the resulting observation-count
distribution, so a globally multi-view point cannot silently become a
mono-view seed inside one resident block.
Native run manifests distinguish source-image split counts from expanded
frame/tile counts; fixed and adaptive crop policies can therefore be compared
without treating four tiles as four independent photos.
The thresholds are recorded in `facade_frame.json` and can be tightened with
`facade_seed_max_reprojection_error` and `facade_seed_min_track_length` when a
mission has noisier poses. The facade training workspace is always exported
explicitly, including when every registered camera passes the incidence
filter, so the selected gate cannot be bypassed accidentally.

The production default is `facade_selection_mode=all`: identical basenames are
content-verified and deduplicated, but no view is removed before SfM unless an
explicit exclusion range is configured. This is the coverage-first choice for
systematic multi-pass facades.

Use `facade_excluded_image_ranges` for known coherent close-detail sequences
that would concentrate sparse points on one ornament instead of improving wall
coverage. Its format is inclusive `START..END`, with multiple ranges separated
by semicolons. Every configured range must match at least one basename, and the
exact excluded count and names are recorded in
`facade_selection_report.json`. The local runner exposes the same rule as the
repeatable `--exclude-image-range START END` option. Do not use this as a
generic density control: only omit a sequence after confirming that the
remaining systematic passes still cover the complete elevation.

For the Cahors Saint-Étienne mission, the validated exclusion is inclusive
from `DJI_20250324162114_0307_V.JPG` through
`DJI_20250324163256_0658_V.JPG`. These 352 rose-window and portal detail views
were left out of the preferred sparse solve. Registration stayed at 97.30%,
occupied facade-grid coverage rose from 73.65% to 78.03%, and the final
DroneGS result improved to 21.616 dB PSNR / 0.564 SSIM. This range is
mission-specific and is therefore documented, not hard-coded as a global
default.

The optional `auto` selection uses the DJI/Autel gimbal pitch. It keeps filename-
contiguous, pitch-stable runs of at least `facade_min_pass_images`, with
`abs(gimbal pitch) <= facade_max_abs_pitch_deg`. Yaw remains unrestricted by
default for a long or articulated facade. When a mission contains several
walls, set the optional `facade_target_yaw_deg`; the circular
`facade_yaw_tolerance_deg` (35° by default) keeps the frontal and useful
oblique views of that wall while rejecting other azimuths. Identical duplicate
filenames are content-verified and deduplicated; different images sharing a
filename are rejected rather than silently overwritten. Use it only when the
uploaded collection contains known unrelated flights and audit
`facade_selection_report.json` before accepting the reconstruction.

## Caspar alignment for large facades

Facade mode defaults to COLMAP's incremental mapper with the GPU Caspar bundle
adjustment backend, a shared `SIMPLE_RADIAL` camera, 4200 px SIFT extraction,
16,384 requested features/matched features and guided matching. Its bounded
pair graph uses at most 48 spatial neighbours, at least 16 when available, and
six temporal neighbours; the mapping budget is four hours. Caspar is a
bundle-adjustment accelerator, not a feature matcher: image completeness still
depends on the input set and match graph. Camera GPS may propose nearby pairs,
but neither GPS/RTK positions nor IMU/gravity are fitted as pose constraints
and no CRS is created.

Caspar currently accepts only `SIMPLE_RADIAL` and `PINHOLE`. If the user
explicitly requests `OPENCV`, validation requires Ceres instead. In `auto`
mode a failed facade Caspar solve reuses the verified matches with Ceres;
aerial/map mode keeps GLOMAP as its primary engine.

## Local frame calculation

After bundle adjustment, DroneAI robustly fits the sparse elevation plane and
uses it when planarity, camera-side consistency and agreement with optimized
optical axes are sufficient. Otherwise it falls back to the robust camera-axis
frame: carved masonry, arches, columns and window reveals can make a local
moulding look like the dominant plane. Image-up vectors define the vertical
axis. The final orientation therefore comes from the visual reconstruction,
not from an IMU gravity prior.

`facade_frame.json` records the origin, rotation matrix, world axes, plane
inlier ratio, plane RMSE, view-incidence distribution and fraction of cameras
on the chosen outward side. These values make a mixed or incorrectly selected
elevation auditable. A low plane-inlier ratio is not by itself a failure on an
ornate facade; camera-side consistency and incidence are the primary frame
checks.

Camera positions may guide bounded *pair selection* so that views from
different passages are matched. They are not used as pose constraints and do
not define the output frame or origin.

## Scale without georeferencing

The output position is never derived from RTK, GCP or an EPSG code. A scale is
still required to interpret a requested pixel size such as 1 cm/pixel:

- `gps-baseline` (default) compares relative 3D GNSS distances (including EXIF
  altitude when available) between cameras with optimized COLMAP baselines.
  Longitude/latitude are never used as an absolute origin and no projected CRS
  is selected;
- `manual` uses `facade_meters_per_model_unit`, obtained from a surveyed length
  or scale bar;
- `model-units` writes an explicitly unscaled local product.

For metrology, a surveyed scale is preferable. Consumer GNSS baseline scale is
usually adequate for visualization and approximate dimensions but is not a
centimetric control. GCP observations are intentionally not fitted in facade
mode; they can be used separately as check measurements if their coordinates
are first expressed in the same local facade frame.

The scale reader prefers the workspace's untouched source images. COLMAP's
undistorted JPEG copies do not reliably preserve DJI metadata and are used only
as a fallback.

## Recommended capture and settings

- capture one dense near-normal grid for texture;
- add one pass from each side at a moderate incidence angle (about 15–30°) for
  stronger geometry;
- maintain at least 70–80% overlap and constant focus/exposure where possible;
- avoid mixing isolated macro/detail shots into the main solve unless they
  have a connected multi-image sequence;
- the facade quality preset uses 4200 px / 16,384 SIFT features and a 16,384
  matched-feature cap, native first
  octave (`0`), guided matching and combined spatial/sequential pairs for
  masonry detail on Mavic 3 Enterprise imagery;
- use 45° as a robust first texture threshold; 30° gives a cleaner frontal
  texture when enough well-observed views remain. Selecting only the nominal
  0° passes for the entire solve is not recommended because it weakens depth;
- facade missions use the same adaptive resident core/buffer process as maps.
  The selected production profile controls the scientific envelope: qualified
  `normal-v3` uses 15,000 iterations, a 3 M scene floor and an 8-pixel density
  target, while candidate `high-quality-v3` uses 30,000 iterations, a 5 M
  floor and a 3.6-pixel target. The detected VRAM ceiling can reduce each
  resident PLY and increase the number of wall-plane blocks without reducing
  the requested total surface density;
- every buffer is trained from native calibrated crops selected by visibility
  in the metric facade plane. `FACADE_HD_V2` remains the versioned fallback
  recipe for direct clients that do not submit a production quality profile;
- DroneAI inserts no software sleep between Gaussian iterations; NVIDIA's
  firmware/driver thermal and power protections remain authoritative;
- request 0.01 m/pixel only after confirming that source GSD, focus and pose
  quality support it. A 0.001 m/pixel export can preserve sub-centimetric
  Gaussian detail on a close-range mission, but cannot restore information
  absent from the source photographs.

Facade held-out views are evaluated with the facade gates established on the
final Cahors reference run (`facade_canary_min_psnr=18`,
`facade_canary_min_ssim=0.25`). The product manifest records the effective
thresholds and the selected production profile. `FACADE_HD_V2` remains the
fallback for direct clients without a selected profile; historical
`FACADE_HD_V1` jobs remain replayable with their fixed 2 M monolithic recipe.
Changing a quality value remains possible but produces an explicitly
customized recipe.

Before rendering, `facade_depth_iqr_multiplier` (default `1.0`) keeps the
robust depth band around the elevation. This removes isolated street,
background or weak-pose Gaussians far behind the wall while preserving normal
architectural relief. Set it to `0` only for an intentionally very deep
structure, or raise it when the frame report shows that valid recesses are
being clipped. The raster footprint itself uses robust 0.1–99.9% bounds plus
a one-metre margin so thin facade borders are retained without letting remote
islands collapse the useful resolution.

## Local diagnostic command

Prepare the unaligned sparse model and undistorted images with:

```bash
./tools/run_local_colmap.sh DATASET WORKSPACE \
  --facade --stage undistort --matcher spatial
```

Then the
same production renderer can be exercised without Kafka or S3:

```bash
./tools/run_local_gaussian.sh WORKSPACE \
  --render-mode facade \
  --profile facade-hd \
  --facade-scale-mode gps-baseline \
  --resolution 0.01
```

The local runner does not require `sparse_geo`, `geo_data.txt.crs` or an
alignment transform for this mode.

## Qualification utilities

Three maintained comparison tools make facade changes reproducible. Compare the
sparse coverage of a candidate reconstruction with a reference model using:

```bash
python3 tools/compare_facade_sparse_distribution.py \
  REFERENCE_SPARSE CANDIDATE_SPARSE \
  --output sparse-comparison.json \
  --preview sparse-comparison.png
```

Compare the rendered elevation with an independent reference raster using:

```bash
python3 tools/compare_facade_rasters.py \
  CANDIDATE.png REFERENCE.png \
  --json raster-comparison.json \
  --preview raster-comparison.png
```

The second report records robust SIFT/homography residuals, global and gridded
coverage, and sharpness on the common valid mask. These measurements qualify a
specific dataset and do not replace an independent surveyed scale check.

When the independent reference is a coloured dense PLY, compare both final
products in one bounded, streamed pass:

```bash
python3 tools/compare_facade_products.py \
  facade_orthophoto.tif facade_orthophoto.height.tif REFERENCE.ply \
  --json facade-product-comparison.json \
  --preview facade-product-comparison.png \
  --reference-preview facade-reference-raster.png
```

The dense reference is memory-mapped and rasterized in chunks in its robust
world-vertical facade frame. Colour alignment uses the same SIFT/homography
gate as the raster-only tool. The candidate depth is then compared in metres
on the common mask after fitting only its unavoidable facade-plane offset and
orientation sign; scale is never fitted. The report includes median, P90/P95
and RMSE depth residuals, correlation and overlap, so a visually sharp but
geometrically deformed elevation cannot pass unnoticed. A PLY reference must
contain scalar `x/y/z` and `red/green/blue` vertex properties in binary
little-endian form.

## Acceptance checks

Before using the raster for measurements, verify:

- cameras predominantly lie on one side of the elevation and the median view
  incidence remains moderate;
- plane RMSE is plausible for the actual facade relief; do not require a high
  plane-inlier ratio on deeply carved architecture;
- window edges and stone courses stay straight across tile boundaries;
- repeated details are not doubled, especially near depth discontinuities;
- at least one known independent facade distance agrees with the raster scale;
- the TIFF has no CRS and is labelled `LOCAL_FACADE` in the product manifest.
