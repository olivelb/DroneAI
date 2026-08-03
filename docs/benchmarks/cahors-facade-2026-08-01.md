# Cahors Saint-Etienne facade benchmark — 2026-08-01

## Scope

This benchmark validates the local, CRS-free facade product against the
Metashape orthophoto `ortho_SAINT-ETIENNE2.tif`. The reference is 1,345 ×
2,112 pixels at exactly 0.01 m/pixel, or 13.45 × 21.12 m. Its EPSG:4978 tag is
incompatible with its small local coordinates and is ignored: the comparison
uses image content and local metric scale only. Metashape is a comparative
reference, not surveyed ground truth.

The resulting production profile is the generic `FACADE_HD_V1`. Cahors is the
qualification dataset, not a product or profile name. Artifact filenames below
retain their historical experiment names for traceability.

Source imagery: `Y:\CAHORS\SAINT-ETIENNE\MAVIC3e`.

- 3,428 JPEG files;
- 2,837 unique images after removing 591 byte-identical duplicates;
- DJI Mavic 3 Enterprise, 5,280 × 3,956 pixels;
- calibrated XMP focal length: 3,725.15 px at native width;
- several walls, five systematic passes and separate close-detail sequences.

## Why the first full solve was rejected

Pitch-only selection retained 2,457 images. COLMAP registered 2,378 (96.78%)
and reconstructed 1,372,104 points at 0.822 px mean reprojection error, but the
model mixed several wall azimuths. Its camera-side ratio was only 64.6%, its
median incidence was 45.2°, and the rendered elevation visibly bent the rose
and portal. Registration percentage alone was therefore not a sufficient
facade acceptance criterion.

The rejection was later traced more precisely to the input staging, not to a
fundamental limit of the full solve. The local copy contained only 2,457 of
the 2,837 unique source images. The 380 omissions included the last 326 images
of the fifth pass (`DJI_..._0333` through `DJI_..._0658`) and smaller tails of
three earlier folders. They extend the camera footprint beyond the previously
reconstructed edge and include near-normal as well as oblique views. In other
words, the left/bottom holes in the Gaussian raster already existed in its
COLMAP seed.

## Coverage-first Caspar rerun

The complete source was restaged by basename and content: 3,428 JPEG files
became 2,837 unique images after 591 byte-identical duplicates were removed.
Every unique image entered feature extraction and mapping. Absolute GPS/RTK,
GCP and IMU/gravity priors were disabled; camera positions were used only to
propose spatial match pairs and later to recover approximate metric scale.
Sequential pairs with overlap 15 complemented 48-neighbour spatial matching.

The rerun used `SIMPLE_RADIAL`, 4,200 px SIFT extraction, 16,384 requested
features and a 16,384 matched-feature cap. The latter is important on the 8 GB
GPU: an initial 32,768-match/64-neighbour attempt reached 7.64 GB and was too
slow, whereas 16,384/48 remained bounded. Caspar then ran COLMAP's robust
incremental mapper with GPU bundle adjustment.

| Sparse metric | Incomplete GLOMAP (2,457 images) | Full Caspar (2,837 images) |
|---|---:|---:|
| Registered cameras | 2,378 (96.78%) | **2,771 (97.67%)** |
| Sparse points | 1,372,104 | **2,385,812** |
| Mean reprojection error | 0.822 px | **0.799 px** |
| Median reprojection error | 0.738 px | **0.721 px** |
| Points with error <= 2 px and track >= 2 | 1,351,099 | **2,335,730** |

Of the 380 restored images, 352 registered. They observe 283,604 accepted
points, including 223,282 accepted points observed exclusively by restored
images. This is direct evidence that the missing pass contributes new edge
geometry rather than merely duplicating the centre. The single optimized focal
length is 3,715.11 px, only 0.27% below the 3,725.15 px DJI calibrated XMP
value; there is no indication that a gross intrinsic drift caused the former
edge loss.

Measured wall times were 34.00 min for feature extraction, 41.73 min for
spatial matching, 13.29 min for the complementary sequential matcher and
62.13 min for Caspar mapping. Caspar's advertised one-to-two-order-of-magnitude
speedup concerns its bundle-adjustment solver relative to Ceres CUDA on
medium/large problems; it does not make SIFT, pair matching, triangulation or
image undistortion ten times faster. On this end-to-end run, completeness was
the decisive gain. See the [COLMAP Caspar FAQ](https://colmap.github.io/faq.html#caspar-gpu-accelerated-bundle-adjustment)
for the upstream scope and current limitations of that claim.

## Close-detail sequence ablation

The inclusive sequence from `DJI_20250324162114_0307_V.JPG` through
`DJI_20250324163256_0658_V.JPG` contains 352 close views of the rose window and
portal. These images were removed before matching to test whether their dense,
localized observations were biasing the sparse seed away from a uniform wall
coverage. The remaining 2,485 images were solved with the same full-resolution
Caspar configuration. GPU scheduling and power management were left entirely
to the driver.

| Sparse metric | Full Caspar | Without 352 detail views |
|---|---:|---:|
| Selected / registered cameras | 2,837 / 2,771 (97.67%) | 2,485 / 2,418 (97.30%) |
| Sparse points | 2,385,812 | **3,009,679** |
| Mean reprojection error | **0.799 px** | 0.814 px |
| Median reprojection error | 0.721 px | **0.712 px** |
| Accepted points (error <= 2 px, track >= 2) | 2,335,730 | **2,907,806** |
| Occupied facade-grid cells | 4,525 / 6,144 (73.65%) | **4,794 / 6,144 (78.03%)** |
| Effective cell ratio | 29.69% | **32.23%** |
| Top 1% cell concentration | 10.06% | **8.63%** |
| Top 5% cell concentration | 36.43% | **30.42%** |
| Top 10% cell concentration | 56.92% | **50.77%** |

The two sparse models were aligned from 2,412 common registered cameras using
a robust similarity transform. Its median camera residual is 0.00118 reference
model units, so the grid comparison is not explained by a loose alignment.
The reduced solve retains 94.74% of the reference model's occupied cells, adds
10.58% candidate-only cells and reaches an occupied-cell intersection-over-
union of 85.19%.

This ablation is retained as the preferred DroneGS seed. It sacrifices only
0.37 percentage point of registration and raises mean reprojection error by
0.015 px, while increasing useful point count and producing a measurably more
uniform facade distribution. Localized sparse density is not a priority here:
DroneGS can densify from the better-distributed seed. The excluded range is
recorded explicitly in `facade_selection_report.json`, `metrics.json` and the
local workspace fingerprint so the result remains auditable and reproducible.

The preferred seed was subsequently validated with the same full facade-
quality profile as the 2,837-image Caspar run: 30,000 iterations, native-detail
images up to 4,096 px, SH3, a two-million-Gaussian cap and 0.01 m output pixels.
The loader retained 2,902,653 quality/proximity-filtered points and the spatially
balanced initializer selected 1,700,000 of them. DroneGS trained from 2,018
texture cameras and finished with 1,884,201 Gaussians in 11,370 s (3 h 09 min).

| DroneGS validation metric | Full Caspar | Without 352 detail views |
|---|---:|---:|
| Held-out PSNR | 21.043 dB | **21.616 dB** |
| Held-out SSIM | 0.555 | **0.564** |
| Initial / final trainer loss | 0.4840 / 0.1604 | **0.4272 / 0.1588** |
| Training duration | 12,874 s | **11,370 s** |
| Mean throughput | 2.330 iterations/s | **2.639 iterations/s** |
| Start-of-run ETA at mean rate | 3 h 34 min 34 s | **3 h 09 min 30 s** |
| Final Gaussians | 1,819,805 | 1,884,201 |
| Raster dimensions at 1 cm | 3,563 × 3,233 | 2,001 × 3,114 |

The held-out camera sets differ because the source collections differ, so the
PSNR/SSIM gain is directional rather than a strict paired-image result. Both
runs pass the production canary. The reduced run focuses its raster envelope
more tightly on the target elevation instead of retaining the surrounding wall
geometry present in the larger full-run raster. Per-view loss telemetry varied
between approximately 0.09 and 0.18 as different cameras were sampled; the run
manifest's comparable aggregate loss decreased from 0.4272 to 0.1588 and is
slightly below the full run's 0.1604 final loss.

Operational monitoring sampled the evolving loss, rolling iterations/second
and remaining-time estimate at five-minute intervals; it did not alter GPU
power, clocks or scheduling. The table reports the reproducible whole-run
throughput because short rolling rates depend on checkpoint, evaluation and
densification intervals. Intermediate losses are view-dependent and are not
treated as a monotonic quality curve; the comparable manifest initial/final
loss and held-out PSNR/SSIM are the acceptance evidence.

Registration of both products to the same Metashape reference provides the
more useful product-level comparison:

| Metashape comparison metric | Full Caspar | Without detail views |
|---|---:|---:|
| Homography residual median | 1.302 px | **1.095 px** |
| Homography residual RMSE | 2.000 px | **1.731 px** |
| Homography residual P90 | 3.425 px | **2.832 px** |
| Nominal reference coverage | 98.15% | **98.33%** |
| Usable reference coverage (threshold 240) | 94.56% | **95.97%** |
| Usable grid cells below 90% coverage | 20 | **10** |
| P90 gradient / Metashape | 126.45% | 124.28% |

The detail-free result is better aligned and more uniformly covered, while its
P90 gradient is 1.8% lower than the full-run product. This is an acceptable
sharpness trade-off: both DroneGS products are more locally contrasty than the
Metashape reference, partly because holes and Gaussian edges inflate gradient
statistics. The far-left margin remains the weakest local coverage cell and
still needs explicit masking or a coverage-aware compositor before the raster
is treated as a seamless deliverable.

The detail-free workspace contains `facade_orthophoto.facade-quality.tif`, its
local-depth companion, the final Gaussian PLY, the complete run/canary reports,
and `facade-quality-vs-metashape.{json,jpg}` plus
`facade-quality-vs-caspar2837.{json,jpg}` for visual and numerical audit.

This failure led to two production changes:

1. an optional `facade_target_yaw_deg` with circular
   `facade_yaw_tolerance_deg` isolates one wall while retaining useful oblique
   views;
2. the final frame uses a robust sparse elevation plane when it is sufficiently
   planar and camera-side consistent, otherwise it falls back to optimized
   camera optical axes.

For this benchmark, the Metashape raster was used only to identify the target
wall. Images were then expanded geometrically from target sparse observations;
the reference did not constrain COLMAP poses or DroneGS training.

## Superseded dedicated-wall experiment

The following 858-image experiment predates the complete coverage-first
Caspar reruns above. It is retained as historical evidence for the frame and
scale decisions, but its `OPENCV`/GLOMAP/Ceres recipe and 2,400 px DroneGS
result are not the current production profile.

The wall-specific set contained 858 images. SIFT was extracted at 3,200 px
with up to 8,192 features, first octave 0, guided spatial matching and one
shared `OPENCV` camera. GLOMAP registered 642 images; the incremental Ceres
fallback registered 635 and was retained because it produced the more locally
coherent model.

| Metric | Dedicated result |
|---|---:|
| Selected / registered | 858 / 635 (74.01%) |
| Sparse points | 661,875 |
| Points after training quality gate | 306,203 |
| Mean reprojection error | 0.921 px |
| Median reprojection error | 0.808 px |
| Facade camera-side ratio | 100% |
| Median / P90 incidence | 26.35° / 36.86° |
| Robust plane inliers | 23.64% |
| Robust plane RMSE | 0.065 m |
| Sparse useful envelope | approximately 13.4 × 21.2 m |

The useful envelope agrees closely with the 13.45 × 21.12 m Metashape
reference. The 95% experimental registration gate was intentionally not used
as a product gate: close details and weak edge views need not all register for
the main wall to be complete.

## Scale and native photo GSD

Relative GNSS camera baselines yield 1.628495 m per COLMAP model unit. GPS is
used only for scale; it does not define the origin, facade normal or output
CRS. Source JPEG metadata is read from the original workspace image directory,
because COLMAP-undistorted copies may lose DJI EXIF/XMP.

Using optimized `OPENCV` focal length (3,476.29 px at native width) and robust
camera-to-plane distance gives:

| Native GSD percentile | mm/pixel |
|---|---:|
| P10 | 0.394 |
| P25 | 0.419 |
| Median | **0.455** |
| P75 | 0.495 |
| P90 | 0.528 |

The requested 10 mm output pixel is therefore about 22 native photo pixels at
the median distance. Output resolution is not the limiting source resolution;
multi-view fusion, occlusion and Gaussian footprint blending are.

## DroneGS resolution comparison

Both runs use 625 cameras within 45° incidence and at least 20 sparse
observations. The trainer now receives the same point-quality gate reported by
the loader (`error <= 1 px`, original track length at least 3), preventing raw
low-quality points from silently exceeding the Gaussian cap.

| Profile | Training image width | Iterations / SH | Held-out PSNR / SSIM | Runtime | Result |
|---|---:|---:|---:|---:|---|
| low-memory | 1,600 px | 5,000 / SH1 | 14.885 / 0.404 | 183 s | coherent but soft |
| high-resolution | 2,400 px | 8,000 / SH2 | 21.248 / 0.521 | 359 s | retained |

The retained high-resolution raster contains 499,441 Gaussians and is 1,495 ×
2,312 pixels at 0.01 m/pixel (14.95 × 23.12 m), with no CRS and a separate
local facade-depth TIFF.

## Comparison with Metashape

DroneAI was registered to Metashape by SIFT and robust similarity/homography.
These are product-to-product measurements, not independent accuracy checks.

| Comparison metric | 1,600 px run | 2,400 px retained run |
|---|---:|---:|
| SIFT ratio-test matches | 117 | 310 |
| Similarity inliers | 56 | 154 |
| Similarity residual median | 2.04 px (2.04 cm) | **1.55 px (1.55 cm)** |
| Similarity residual RMSE | 2.24 px | 2.32 px |
| Metashape content covered | 70.3% | **77.3%** |
| Laplacian variance / Metashape | 58.9% | **88.0%** |
| P90 gradient / Metashape | 82.0% | **89.0%** |
| Relative raster scale | 0.9624 | 0.9596 |

The high-resolution run is a clear improvement: more repeatable details,
sharper rose/portal geometry and better effective coverage. Metashape remains
superior in continuous masonry coverage, edge completeness, photometric
uniformity and fine texture. The approximately 4% relative scale discrepancy
is consistent with using ordinary relative GNSS baseline scale; a surveyed
facade length is required before dimensional use.

Artifacts copied to the benchmark output directory:

- `CAHORS_FACADE_DEDICATED_OPENCV_HIGHRES_1CM.tif`;
- `CAHORS_FACADE_DEDICATED_OPENCV_HIGHRES_DEPTH_1CM.tif`;
- `CAHORS_FACADE_DEDICATED_OPENCV_HIGHRES_preview.webp`;
- `DEDICATED_OPENCV_HIGHRES_vs_METASHAPE.jpg`;
- `dedicated_opencv_highres_vs_metashape.json`;
- `facade_frame_dedicated_opencv.json`.

## Remaining limitations and best next steps

1. Supply one surveyed facade length and use manual scale; GNSS scale is about
   4% different from this Metashape product.
2. Add a foreground visibility/connected-component mask to explicitly mark
   the remaining weak far-left margin instead of blending it toward white.
3. Add exposure compensation and a facade-specific sharp texture compositor;
   Gaussian color fusion alone remains softer than Metashape on flat masonry.
4. Use the target-yaw control only when a mission genuinely mixes target
   walls; the preferred Cahors seed instead keeps every systematic pass and
   excludes only the documented 352-image close-detail sequence. Registration
   ratio must be evaluated with camera-side ratio, incidence, plane RMSE and
   target envelope.
5. Treat the 1.55 cm matching residual as cross-product consistency only.
   Surveyed targets or check distances are still required for an accuracy
   claim.
