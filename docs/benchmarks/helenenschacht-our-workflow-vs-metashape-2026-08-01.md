# Helenenschacht — OUR_WORKFLOW versus Metashape

- Date: 2026-08-01
- Base revision: `c8d0464`, plus the counter-audit fixes in this change set
- Dataset: 176 Autel Robotics XT705 images
- Camera priors: 176/176 corrected MRK positions with ENU covariance
- Checkpoints: 5 surveyed GCP, annotated in 35 source images
- Horizontal comparison CRS: `EPSG:32633`
- GCP policy: verification only; no GCP participates in pose, intrinsic,
  sparse, dense or Gaussian optimization
- Metashape: 2.3.1.22398, RTK camera references enabled, no GCP marker in the
  project at the time of the export

## Executive verdict

On this bounded surface, OUR_WORKFLOW is more accurate horizontally and
vertically than the current Metashape project. The final DroneGS orthomosaic
measures **6.24 cm horizontal RMSE** from reconstructed target centres, versus
**14.88 cm** for the directly visible centres in `TEST_ORTHO.tif`. The
independent sparse result, 6.32 cm, agrees with the raster measurement.

Metashape reconstructs 192,850 tie points against 58,454 for the selected
COLMAP sparse, but density does not imply geospatial accuracy. Direct
triangulation of the 35 GCP observations through the Metashape camera solution
shows a systematic vertical error of about **-2.77 m**. Once that forbidden
GCP-derived offset is removed, its residual vertical dispersion is about
10.4 cm, close to the 10.1 cm centred dispersion of OUR_WORKFLOW. The main
Metashape failure is therefore the absolute vertical datum of the reconstructed
surface, followed by a larger horizontal deformation.

These figures qualify this dataset and these exact recipes. They are not a
generic accuracy certificate for either engine.

## Products compared

### OUR_WORKFLOW

The selected 3D recipe uses:

- SIFT CUDA at 3,200 px, 8,192 features and first octave 0;
- guided matching, deterministic mapper seed and two global BA passes;
- historical retriangulation thresholds, 15 px / 15° / 1°;
- covariance-aware RTK pose-prior refinement, 25 iterations;
- Cauchy loss scale 62.56;
- DroneGS, 30,000 iterations, 2 million Gaussian cap, SH3, source width
  3,200 px and requested raster GSD 5 mm.

The final RGB and DSM are local benchmark artefacts and are intentionally not
versioned because they occupy several gigabytes.

### Metashape

The comparison uses the completed sparse camera solution and the exported
orthomosaic `TEST_ORTHO.tif`:

- 192,850 exported tie points;
- output CRS `EPSG:4326`;
- effective ground sampling around 12.2 mm/pixel at the site latitude;
- camera references enabled with the RTK uncertainties stored by Metashape;
- no imported GCP marker, so the five surveyed targets remain independent.

The dense/model calculation continuing after the export does not by itself
change the already solved sparse poses or remove their datum bias.

## Sparse checkpoint comparison

The 35 manually annotated target centres are triangulated from each camera
solution, then compared with the surveyed coordinates.

| Metric | OUR_WORKFLOW sparse | Metashape sparse |
|---|---:|---:|
| Sparse points | 58,454 | **192,850** |
| Horizontal RMSE | **6.320 cm** | 16.006 cm |
| Vertical RMSE | **15.741 cm** | 276.737 cm |
| 3D RMSE | **16.962 cm** | 277.200 cm |
| Mean signed vertical error | +12.071 cm | -276.543 cm |
| Vertical RMSE after removing the mean bias | 10.102 cm | 10.377 cm |

Removing the mean bias is diagnostic only. It uses the checkpoints and would
violate the verification-only policy if applied to the deliverable.

## Orthomosaic centre comparison

The official coordinates below come from the WGS84 values in `gcp_list.txt`,
transformed to UTM 33N. They are not the two-decimal UTM display values in the
companion CSV.

Metashape centres are measured directly at the four-quadrant junction in its
orthomosaic. DroneGS centres are reconstructed on a common 5 mm UTM grid by
matching the sharp target geometry at several blur levels in luminance,
chromaticity and gradient space. Visible DroneGS junctions are independently
refined. Target 2 is saturated and target 3 has lost most internal contrast;
their realistic centre uncertainty is about 3 cm and 2.5 cm respectively.

| GCP | Surveyed E / N (m) | Metashape centre E / N (m) | Meta H | DroneGS centre E / N (m) | DroneGS H |
|---|---|---|---:|---|---:|
| 1 | 610852.3942 / 5277733.5909 | 610852.3039 / 5277733.7226 | 15.96 cm | 610852.3467 / 5277733.6184 | **5.49 cm** |
| 2 | 610872.9579 / 5277729.7356 | 610873.0157 / 5277729.8406 | 11.99 cm | 610873.0029 / 5277729.7231 | **4.67 cm** |
| 3 | 610858.0791 / 5277710.7247 | 610857.9940 / 5277710.7188 | 8.53 cm | 610858.0316 / 5277710.7122 | **4.91 cm** |
| 4 | 610862.4096 / 5277691.9517 | 610862.4105 / 5277691.8227 | 12.90 cm | 610862.4171 / 5277691.9142 | **3.82 cm** |
| 5 | 610843.8857 / 5277696.1650 | 610843.7010 / 5277696.0518 | 21.66 cm | 610843.7982 / 5277696.1125 | **10.20 cm** |

| Aggregate | Metashape ortho | DroneGS ortho |
|---|---:|---:|
| Mean horizontal error | 14.21 cm | **5.82 cm** |
| Median horizontal error | 12.90 cm | **4.91 cm** |
| Horizontal RMSE | 14.88 cm | **6.24 cm** |
| Maximum horizontal error | 21.66 cm | **10.20 cm** |

DroneGS and its source sparse differ locally by roughly 2–4.4 cm at the target
centres, which is compatible with Gaussian blur, splatting and raster
interpolation. Their aggregate RMSE is nearly identical: 6.24 versus 6.32 cm.

## Final DSM validation

The selected 3D profile was rendered to a 5 mm float32 DSM with `nodata=NaN`.
Only one raster pixel is read at each surveyed checkpoint.

| Metric | OUR_WORKFLOW final DSM |
|---|---:|
| Successful checkpoints | 5/5 |
| Vertical MAE | **10.354 cm** |
| Vertical median absolute error | 10.790 cm |
| Vertical RMSE | **11.444 cm** |
| Vertical maximum | 16.246 cm |

Compared with the earlier 2,400 px RTK GeoTIFF, the selected profile reduces
DSM RMSE by 28.9%. Compared with the 2,400 px no-RTK witness, it reduces DSM
RMSE by 50.4%.

## What the comparison does and does not prove

It proves, for the tested site, that:

1. the GCP targets are independently observable in both source images and
   final orthomosaics;
2. the DroneGS raster preserves the horizontal accuracy of its COLMAP source;
3. increasing tie-point density alone did not improve absolute accuracy;
4. the current Metashape solution has a large absolute vertical bias and a
   larger horizontal deformation;
5. RTK priors plus the 3,200 px recipe materially improve DSM accuracy, but do
   not make that recipe the best pure-XY preset.

It does not prove that:

- five checkpoints predict performance on another site or camera;
- 5 mm output GSD means 5 mm geospatial accuracy;
- every blurred Gaussian target admits centimetric centre recovery;
- a differently calibrated or re-optimized Metashape project would retain the
  same errors;
- the ellipsoidal heights can be mixed with an orthometric deliverable without
  an explicit vertical transformation.

## Weaknesses and improvement plan

### OUR_WORKFLOW

1. **Independent GCP gates are offline.** The evaluator exists, but the worker
   does not yet promote or reject a product from horizontal and vertical
   checkpoint thresholds. Integrate this as a post-product gate while keeping
   GCP out of optimization.
2. **DroneGS softens survey targets.** Preserve a source-image texture layer or
   render a conventional orthophoto alongside the Gaussian product when
   centimetric target reading matters.
3. **The 3D preset sacrifices some XY.** Keep 2,400/4,096 without guided
   matching for planimetry; use 3,200/8,192 + RTK-62.56 for DSM/volume work.
4. **GPU SIFT is not bitwise repeatable.** Attribute RTK gains only from pairs
   that share the same initial sparse/database, and add repeated cross-flight
   releases rather than relying on one seed.
5. **Raster extent depends on model extrema.** Add an explicit common render
   extent for strict A/B comparisons and stable tiling.
6. **DroneGS trains in local model units.** Normalize the sparse to metric units
   before training, or scale spatial hyperparameters with the Sim3.
7. **Orientation metadata is unused.** Integrate IMU/gimbal priors only after
   aircraft-specific body, gimbal and camera frame conversions are validated.

### Metashape comparison project

1. Duplicate the project after the current model finishes; do not modify the
   active run used by this benchmark.
2. Re-run an RTK-only optimization A/B with reviewed camera accuracy,
   calibration groups and gradual intrinsic parameter release.
3. Keep all five GCP as disabled checkpoints during tuning. Enabling them as
   controls would answer a different question.
4. Export the same projected CRS, fixed metric extent and resolution as
   OUR_WORKFLOW before another raster comparison.
5. Report camera-reference residuals, marker reprojection residuals and product
   centre errors together; no single one is an accuracy certificate.

## Recommended operating compromise

- **XY/orthomosaic priority:** the 2,400 px survey profile, 4,096 features,
  guided matching disabled and the general RTK loss scale 7.82.
- **DSM/volume priority:** `Precision 3D · RTK`, 3,200 px, 8,192 features,
  guided matching and RTK loss scale 62.56.
- **Acceptance:** independent checkpoints after product generation, with
  separate horizontal and vertical thresholds.

Machine-readable values are stored in
[`helenenschacht-our-workflow-vs-metashape-2026-08-01.json`](helenenschacht-our-workflow-vs-metashape-2026-08-01.json).
The complete RTK, feature-density and DSM ablations remain in
[`helenenschacht-rtk-geotiff-ab-2026-07-31.md`](helenenschacht-rtk-geotiff-ab-2026-07-31.md).
