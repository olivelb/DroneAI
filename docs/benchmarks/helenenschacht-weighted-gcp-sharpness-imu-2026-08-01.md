# Helenenschacht: weighted GCP, DroneGS sharpness, and IMU gravity

Date: 2026-08-01

This report records three independent changes. Ground control can now be used
for adjustment with user-declared uncertainty; the orthographic rasterizer no
longer applies the former uncompensated 0.3 px² dilation; and Autel/DJI gimbal
attitudes can be converted to COLMAP gravity priors for an explicit A/B test.

## 1. Covariance-weighted ground control

COLMAP's supported pose-prior path constrains camera positions and covariance,
not surveyed 3D scene points. DroneAI therefore does not pretend that GCP are
native COLMAP bundle-adjustment observations. It triangulates each marked GCP
from the optimized camera model, propagates the image-centre uncertainty
through the ray intersection, combines it with the survey XYZ covariance, and
fits a robust Cauchy Sim(3) in normalized standard deviations.

The dataset may contain:

- `gcp_list.txt`, in the OpenDroneMap convention already used by the checkpoint
  evaluator;
- optional `gcp_accuracy.csv`, with `point_id`,
  `horizontal_accuracy_m`, `vertical_accuracy_m`, `image_accuracy_px`, and an
  optional `role` equal to `adjustment`, `checkpoint`, or `disabled`.

The mission defaults apply only when a point is absent from the CSV. Smaller
one-sigma values give the observation more influence; a 3-sigma Cauchy loss
still protects the transform against a wrongly identified target. Checkpoints
are reported but never enter the fitted transform.

Helenenschacht diagnostic split: points 1, 2, and 5 for adjustment; points 3
and 4 as independent checkpoints; 2 cm horizontal, 3 cm vertical, and 0.5 px
marking standard deviations. The exact input is
[`data/helenenschacht-gcp-accuracy-3-adjustment-2-checkpoint.csv`](data/helenenschacht-gcp-accuracy-3-adjustment-2-checkpoint.csv).

| Result | Value |
| --- | ---: |
| Adjustment weighted RMSE | 0.624 sigma |
| Adjustment Euclidean RMSE | 2.19 cm |
| Checkpoint 3 horizontal / vertical | 5.91 cm / 11.06 cm |
| Checkpoint 4 horizontal / vertical | 5.91 cm / 12.28 cm |

The covariance is recomputed for three iterations as scale and orientation
converge. The published report includes survey, ray-triangulation, and combined
effective XYZ standard deviations plus each residual expressed in sigma units,
so the actual weight applied to every control remains auditable.
The fitted Sim3 is also written into the published `colmap/sparse_geo` model;
the sparse model and GeoTIFF therefore share the same adjusted frame.

Five targets are sufficient to perform a small-site adjustment, but using all
five as controls leaves no independent accuracy claim. The recommended split
is three well-distributed controls plus two checkpoints; use all five only when
absolute placement is more important than independent verification.

## 2. Orthographic sharpness A/B

The old renderer added `0.3 I` to every projected 2D covariance without the
determinant opacity compensation described by Mip-Splatting. That widens each
splat. DroneAI now exposes a compensated Mip-filter variance and defaults to
0.03 px², selected from real target crops rather than from a synthetic image.

Five 6 m × 6 m crops centred on the surveyed targets were rendered from the
same 1,853,537-Gaussian, 3,200 px, 30,000-step model.

### 5 mm output

| Renderer | Gradient RMS | Laplacian variance | Delta Laplacian vs legacy |
| --- | ---: | ---: | ---: |
| Legacy 0.3, uncompensated | 0.011150 | 0.00011171 | — |
| No low-pass filter | **0.011338** | **0.00014914** | +33.5% |
| Compensated 0.03 | 0.011218 | 0.00012884 | **+15.3%** |
| Compensated 0.1 | 0.011030 | 0.00010475 | -6.2% |

### 1 cm output

| Renderer | Gradient RMS | Laplacian variance | Delta Laplacian vs legacy |
| --- | ---: | ---: | ---: |
| Legacy 0.3, uncompensated | 0.020632 | 0.00065962 | — |
| No low-pass filter | **0.021610** | **0.00094452** | +43.2% |
| Compensated 0.03 | 0.021201 | 0.00082883 | **+25.7%** |
| Compensated 0.1 | 0.020486 | 0.00067138 | +1.8% |

The unfiltered output maximizes an edge-energy metric but has no anti-aliasing
protection. Compensated 0.03 is the retained compromise. It is a measurable
improvement, not a cure: the model was trained at 3,200 px from 5,472 px input,
and the 45 m flight with a calibrated focal length near 4,404 px has a native
ground sampling of about 1.02 cm/px. A 5 mm GeoTIFF therefore oversamples the
source information by roughly two times even at full input resolution, and by
about 3.4 times after 3,200 px training resize.

The automatic `gs_data_factor` logic was also corrected. Image count no longer
silently reduces spatial resolution; tiling and the Gaussian cap handle memory,
while the factor now preserves every pixel that `gs_max_width` can consume.
The dashboard Detailed preset uses 3,200 px and a 3 M cap. For this flight, the
recommended production output is 1 cm/px; use 5 mm only as an explicitly
labelled oversampled visualization.

A source-photo reprojection prototype doubled gradient energy, but introduced
seams, ghost objects, and could erase a visible GCP when the selected source
view was inconsistent. It was rejected and is not part of the production path.
The next research candidate is surface-consistent 2D Gaussian/orthogonal
splatting or a visibility-aware seamline optimizer, not post-hoc sharpening.

## 3. IMU/gimbal gravity

All 176 Autel XT705 images carry complete flight and gimbal attitude XMP. The
gimbal yaw/pitch optical direction was compared with the final visual camera
axes:

| Attitude validation | Angular error |
| --- | ---: |
| Median | 0.56° |
| P95 | 1.72° |
| Maximum | 2.17° |

The conversion writes gravity in COLMAP camera axes (X right, Y down, Z
forward). It activates only with at least 95% complete attitude coverage and is
consumed only by `global_mapper --GlobalMapper.ra_use_gravity=1`. Bundle
adjustment remains free to refine every rotation.

The same 2,400 px / 4,096-feature database and 2,906 verified relative poses
were reconstructed with and without gimbal gravity:

| Metric | Visual only | Gimbal gravity | Difference |
| --- | ---: | ---: | ---: |
| Registered images | 176 | 176 | 0 |
| Sparse points | 24,326 | 24,325 | -1 |
| Mean reprojection error | **1.337297 px** | 1.337606 px | +0.000309 px |
| Horizontal GCP RMSE | 5.1090 cm | **5.1079 cm** | -0.0011 cm |
| Vertical GCP RMSE | 29.9365 cm | **29.9103 cm** | -0.0262 cm |
| Mapping runtime | **26.99 s** | 31.82 s | +17.9% |

The accuracy change is negligible and runtime regresses. Gravity support is
therefore implemented, reported in `imu_gravity_report.json`, and disabled by
default. It is an expert recovery option for weak/rotated imagery, not a
default accuracy enhancer. The source Savères drive was not mounted during
this audit, so the Mavic 3E conversion must be validated before enabling it on
that aircraft.

## Primary references

- [COLMAP pose priors, known intrinsics, geo-registration, and EXIF gravity](https://github.com/colmap/colmap/blob/main/doc/faq.rst)
- [Mip-Splatting official implementation and paper](https://github.com/autonomousvision/mip-splatting)
- [Original 3D Gaussian Splatting implementation](https://github.com/graphdeco-inria/gaussian-splatting)
- [TOrtho-Gaussian project](https://gwen233666.github.io/Ortho-Gaussian/)
- [2D Gaussian Splatting official implementation](https://github.com/hbb1/2d-gaussian-splatting)
