# Geometry diagnostics: fixed cameras, explicit support, independent references

This offline tool measures an existing Gaussian PLY without training, COLMAP
reconstruction, registration, pose refinement, or model modification. It is a
measurement component, not a geometric regularizer or a claim of surface accuracy.
The training forward specialization does not allocate diagnostic buffers.

## Generalization contract

- No façade plane, vertical direction, Saint-Étienne path, scene-unit threshold,
  dataset identifier, or fitted metric weight occurs in the algorithm.
- Freeze `views.json` before comparing models. Its cameras and pixel footprints
  are independent of training cells. Render the assembled model for a multi-cell
  comparison; rendering only one cell measures its coverage, not the final scene.
- Training uses the COLMAP/CASPER reconstruction. Diagnostics normally use its
  undistorted calibration/poses; an external-reference comparison may instead
  render the unchanged model at reference cameras transformed into its world frame.
  This changes evaluation cameras only. Crop offsets and resize factors must update
  principal points and focal lengths. Pixel centers are `(x+.5,y+.5)`.
- A geometry-derived candidate depth is not independent truth. Compare against
  exact synthetic references first, then external real references with settings
  frozen before those validation datasets.
- No scalar pass/fail surface-quality score is defined. Coverage, disagreement,
  thickness, normal support, reference accuracy, appearance and cost remain separate.

## Build and run

The native target `dronegs_geometry_probe` is built with DroneGS. Python tooling
requires NumPy (already declared by the COLMAP/dev requirements). Select an
interpreter with NumPy for CMake's `Python3_EXECUTABLE` when running its CTests.

```bash
python3 app1-colmap/dronegs/tools/geometry_diagnostics.py prepare-colmap \
  --binary /path/to/build/dronegs_geometry_probe \
  --colmap-path /path/to/undistorted-dataset \
  --image-ids 12 18 24 --downsample 2 --output /new/fixed-views

python3 app1-colmap/dronegs/tools/geometry_diagnostics.py run \
  --binary /path/to/build/dronegs_geometry_probe \
  --ply /path/to/assembled/point_cloud.ply \
  --views-json /new/fixed-views/views.json --raster-profile fastgs \
  --output /new/diagnostic-run
```

`prepare-colmap` reads the existing binary sparse model; it does not run COLMAP
or load photos/point-cloud geometry. Only PINHOLE and SIMPLE_PINHOLE are supported;
distorted models fail explicitly. The native reader expects a standard sparse
layout including `points3D.bin`, but does not read that file's payload. Explicit
image IDs and optional `--crop X Y W H` select the footprint. Image-region files
used by training do not silently change evaluation cameras. A manifest can also
be supplied directly for exact synthetic cameras or independently chosen crops.

The camera manifest has schema 1, a `views` array and explicit directed `pairs`.
Each view carries `id,width,height,fx,fy,cx,cy,native_fx,native_fy,rotation,translation`.
`rotation` is a 3×3 world-to-camera matrix, translation a length-3 vector. Native
focals refer to the original undistorted source sampling before this diagnostic's
downsampling. Pair selection must be frozen from poses/visibility expectations,
not selected after seeing model errors. At most 32 views, 128 pairs and 16M pixels
per view bound this offline operation. `prepare-colmap` makes all directed pairs;
use at most 11 views there, or supply a larger manifest with a bounded pair list.

## Output contract, version 1

`native/ID.geometry.f32` contains H×W×12 interleaved little-endian float32 values:

| Index | Quantity | Interpretation |
|---|---|---|
| 0 | coverage | 1−final transmittance; soft support, not a visibility oracle |
| 1 | center_z | contribution-weighted mean camera-Z of centers |
| 2 | center_std_z | weighted center-Z standard deviation, stable online moments |
| 3 | covariance_sigma_z | square root of weighted camera-Z marginal covariance |
| 4 | minimum_axis_sigma | RMS shortest-axis sigma; intrinsic thinness only |
| 5–7 | normal_x/y/z | normalized mean candidate normal in camera frame |
| 8 | normal_support_coherence | magnitude of supported normal sum / contribution mass |
| 9 | plane_z | weighted local tangent-plane/ray intersection depth, experimental |
| 10 | plane_std_z | spread of supported plane intersections |
| 11 | plane_support | absolute contribution mass supporting valid plane intersections |

Contributions use the selected renderer's projected footprint, alpha, front-to-back
transmittance and termination. Plane/normal support multiplies contribution weight
by `1 − sigma_min²/sigma_mid²`. This continuous eigenvalue gap is zero for spheres
and line-like splats with two equal smallest axes. It is structural support, **not
a calibrated probability of correctness**. Normal signs face the camera for
aggregation. Plane rays with grazing cosine at most 1e−4 or nonpositive intersection
are unsupported. All depths are camera Z, not Euclidean range.

Zeros at unsupported pixels must never be scored as exact zero error. Coverage
and plane support are compulsory companions to depths. A plane mean can still
fall between layers: its spread is retained. Candidate normal disagreement may
indicate an edge, curvature, or a failure. No global planarity assumption is made.
Covariance Z spread increases on inclined surfaces even if they are thin; it is
not a direct measure of thickness along the true normal. Shortest-axis sigma is
a lower bound on thickness along any direction, not distance to the true surface.

`native/ID.rgb.f32` preserves the associated linear float RGB render. The FastGS
profile is tested against the persistent training renderer. Reference profile
parity is also tested. RGB backgrounds are black; no photometric scores are
computed here and no original images are enlarged.

### Per-Gaussian normals

`native/gaussian_normals.f32` has N×6 float32 values in input PLY vertex order:
world normal xyz, planarity support, shortest-axis sigma, middle-axis sigma.
Normal sign is canonical (largest absolute component positive), **not outward**.
Zero support has a zero normal. Other near-degenerate normals remain low-support.
The sidecar is tied to the exact model SHA256 and has its own checksum. It must be
regenerated after model changes/densification; row indices are not stable IDs
across training snapshots. Normals are derived from existing rotation/scales,
not extra trainable variables that can diverge from the ellipsoid geometry.

## Measurements and reference data

The versioned policy defaults are coverage/support ≥0.5 for reporting, and relative
Z discrepancy >1% for classifying possible occlusion/foreground conflicts. These
are transparent diagnostic categories, **not acceptance tolerances**, and were not
fitted to the real dataset. Every comparable residual remains in the report.
Counts of missing source, out-of-frame, missing target and failed back-projection
are retained. Unsupported metrics are null, never a perfect score.

Cross-view reprojection uses fixed poses and nearest target pixels, with up to
sqrt(0.5) target-pixel sampling displacement. Even exact curved surfaces can have
depth/normal residuals near silhouettes. Report scale and sampling; do not interpret
the 1% category as true occlusion or geometric failure. No pixel-shift optimization
or pose adjustment hides misregistration. Center and plane diagnostics stay separate.

The native-pixel surface footprint is computed from tangent-plane ray derivatives,
including obliqueness. It normalizes intrinsic thickness for scale/resolution
comparison; it is **not** triangulation uncertainty, survey accuracy, or a resolution
claim. Pose uncertainty, baseline and texture are additional factors not estimated
by this component.

For reference comparisons, `--reference-dir DIR` reads `ID.npz` files with
`depth_z` (H×W), `normal_world` (H×W×3) and boolean `valid` (H×W). The optional
boolean `normal_valid` (H×W) must be a subset of `valid`; when omitted it defaults
to `valid`. Positive finite depths are required wherever `valid` is true. Finite
unit normals are required only where `normal_valid` is true. A missing normal does
not discard a valid depth or its error. Both arrays must use exactly the manifest
cameras/pixel grid and common world frame.

Each view reports `reference` for plane-intersection depth/support and
`center_reference` for center depth/coverage. Both include absolute Z error on
all supported valid reference depths. Point-to-reference tangent distance additionally
requires a valid reference normal; unoriented angular error also requires Gaussian
normal support. The separate `reference_pixels`, `matched_pixels`,
`reference_normal_pixels`, `tangent_matched_pixels` and per-metric sample counts
keep these denominators explicit. Missing metric support yields null statistics.
Files are checksummed. This is visible-surface comparison, not a full bidirectional
3D completeness metric. Point/mesh reference datasets need an independently
validated rasterization adapter. Raw Gaussian normals and reference points at
different locations cannot be compared row-by-row.

### Estimated Metashape depth adapter

`tools/geometry_reference.py` prepares existing estimated depths for the comparison
above. It does not rerun photogrammetry, train a model, modify training cameras, or
fit a transformation to Gaussian positions or surface errors. Its input contract is:

- A matched-pose NPZ containing `colmap_ids`, `metashape_ids` and corresponding N×3
  `colmap_centers`, `metashape_centers`, in a fixed order chosen before scoring.
- A raw-depth JSON manifest with positive `meters_per_internal_unit` and `cameras`.
  Each camera carries its unique `key`, `label`, 4×4 camera-to-world `transform`,
  positive integer `source_width/source_height`, and `calibration` with depth-grid
  `width,height,f,cx,cy,b1,b2,k1..k4,p1,p2`. Here `cx,cy` are principal-point offsets
  from the grid center; distortion/affine terms must be zero. Nonempty `ray_checks`
  contain API `pixel`/`ray` pairs verifying the pinhole convention with ray Z=1.
- Sibling `KEY.f32` files containing H×W little-endian float32 camera-Z values in
  Metashape internal units, plus the previously frozen diagnostic `views.json`.

```bash
python3 app1-colmap/dronegs/tools/geometry_reference.py \
  --matched-poses /path/to/matched-poses.npz \
  --depth-manifest /path/to/raw-depths/manifest.json \
  --footprints /path/to/fixed-views/views.json \
  --output /new/reference-preparation

python3 app1-colmap/dronegs/tools/geometry_diagnostics.py run \
  --binary /path/to/build/dronegs_geometry_probe \
  --ply /path/to/assembled/point_cloud.ply \
  --views-json /new/reference-preparation/views.json \
  --reference-dir /new/reference-preparation/references \
  --raster-profile fastgs --output /new/reference-diagnostic
```

The adapter fits one proper similarity `M = s R C + t` from camera centers only.
Every fifth matched row and all evaluation camera IDs are excluded from fitting;
fit/validation residuals and IDs are retained in `alignment.json`. No ICP,
per-camera adjustment, or surface-dependent alignment hides disagreement. Evaluation
camera centers and orientations are transformed back into the unchanged COLMAP
model frame. The former angular crop is transferred to the depth-map grid and
clipped to its available extent before depth errors are examined. Original image
sizes determine native sampling; the depth grid is never enlarged.

Raw depths are divided by `s`; multiply the resulting model-space lengths by
`source_to_meters = s * meters_per_internal_unit` for metric distances. Metashape's
axial Z convention was qualified with API 2.3.1 build 22398 using an exact synthetic
plane and direct stored-depth/mesh-ray comparisons on three exported cameras.
The official Agisoft [depth import script](https://github.com/agisoft-llc/metashape-scripts/blob/master/src/import_depth.py)
also specifies camera-Z inputs, while its
[depth export script](https://github.com/agisoft-llc/metashape-scripts/blob/master/src/export_depth_maps_dialog.py)
uses `chunk.transform.scale` for metric conversion. No ray-length or cosine
correction is applied. Qualify any new export format/API convention before reuse.
This establishes units and convention, not the absolute accuracy of the surfaces.

Depths remain unsmoothed and uninpainted, with invalid depths explicitly masked.
Normals are estimated from central differences with a valid four-neighbor stencil
and a declared 1% relative neighbor-depth jump gate; boundary/discontinuity pixels
lose normal support without losing valid depth support. These are normals derived
from estimated depths, not independent normal ground truth. Metashape and
COLMAP/CASPER may share photographs and have different pose errors; metric-scale
validation does not make estimated depths exact. Report camera disagreement,
sampling, coverage, geometric errors and appearance separately.

Preparation retains alignment, raw-depth checksums, source-manifest/footprint/pose
checksums and adapter checksum. Compare models using the same prepared manifest
and references; do not retune the alignment or normal gate to improve scores.

## Validation and limits

CUDA tests cover analytic separated layers, inclined plane intersections,
covariance thickness, normal degeneracy, empty/behind-camera views, unit/rotation
equivariance, immutable input, reference RGB and persistent FastGS RGB parity.
Python tests cover exact planes/cylinders, depth and normal corruption, holes,
occlusion reporting, sampling sensitivity, crop/calibration, native footprint,
malformed arrays, and binary COLMAP → PLY → native normals → report integration.
The CPU-only CTest `dronegs_geometry_reference_tests` additionally covers known
Sim3 units/rotations, camera inversion, degenerate/invalid inputs, world-frame plane
normals, depth/normal masks, center/plane comparisons and complete synthetic
reference preparation with reserved cameras and provenance. It runs Python with
warnings treated as errors and requires no native binary or GPU.

Output paths must be new. Failed outputs/logs are retained; `status.json` and the
completion manifest distinguish failure from success. Input model hashes are
checked before and after. The report includes binary/tool/view/model provenance.
This lot does not implement a training surface loss, automatic stopping,
densification policy, external normal-dataset download, or certified depth.
