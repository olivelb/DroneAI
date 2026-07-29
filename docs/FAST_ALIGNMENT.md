# Fast aerial alignment

DroneAI's modern alignment path targets COLMAP 4.1.1 and uses:

- a bounded GPS/temporal pair graph instead of exhaustive matching;
- SIFT CUDA extraction and brute-force matching at 1600 px by default;
- optional ALIKED N16Rot/N32 frontends for smaller datasets or higher-end GPUs;
- GLOMAP as the primary global SfM engine;
- incremental mapping with Caspar BA as the automatic fallback when the
  database cameras are `SIMPLE_RADIAL` or `PINHOLE`;
- incremental Ceres CUDA only when a camera model is not supported by Caspar;
- an explicit mapping time budget and registration quality gate.

The pipeline never automatically launches exhaustive matching or an unbounded
CPU bundle adjustment.

## Build

From the cloned DroneAI repository:

```bash
bash setup_deps.sh
docker build --network=host --progress=plain \
  -t drone-colmap-base:latest \
  -f app1-colmap/Dockerfile.base .
docker build --network=host --progress=plain \
  -t drone-colmap:latest \
  -f app1-colmap/Dockerfile .
```

`setup_deps.sh` pins and checksum-verifies COLMAP, PoseLib, faiss, ONNX Runtime,
ALIKED and LightGlue. The ONNX models are embedded in the image, so production
workers do not download models at runtime.

## ALBAGNAC local validation

Mount the Windows drive in the interactive WSL session if it is not already
available:

```bash
sudo mount -t drvfs Y: /mnt/y
```

If `Y:` is a mapped network drive that WSL cannot see, mount its UNC source
instead:

```bash
sudo mount -t drvfs '\\server\share' /mnt/y
```

The two ALBAGNAC flights can be selected from their common parent without
copying or modifying the source photographs:

```bash
tools/run_local_colmap.sh \
  /mnt/y/PHOTOS_ALBAGNAC_MAVIC3E \
  "$HOME/droneai-workspaces/albagnac-fast" \
  --stage align \
  --include-prefix DJI_202306011707_001_Oblique8 \
  --include-prefix DJI_202306011738_002_Oblique9 \
  --engine auto \
  --matcher gps \
  --feature-type SIFT \
  --matcher-type SIFT_BRUTEFORCE \
  --camera-model SIMPLE_RADIAL \
  --gps-quality standard \
  --mapping-timeout-seconds 1200
```

The local runner copies images to the Linux workspace with eight concurrent
readers by default. This is faster than letting COLMAP decode the 1,992 JPEGs
sequentially through the high-latency `drvfs/9p` mount. The source remains
read-only. Pass `--image-staging-mode symlink` to avoid the 25.15 GB local copy
when storage matters more than runtime, or tune `--image-copy-workers`.

On the RTX 4070 Laptop used for ALBAGNAC, ALIKED N16Rot at 1600 px consumed
about 7.9 GB VRAM and measured roughly 11 seconds per image. SIFT LightGlue
also measured only about 3.7 pairs per second at roughly 7.7 GB VRAM. Neither
is therefore the default for this 1,992-image dataset. SIFT CUDA at 1600 px
processes the locally staged images close to one image per second, and the
bounded GPS graph keeps brute-force matching tractable.

The mapping timeout is shared by GLOMAP and its incremental fallback. The
fallback receives only the unused portion of the 20-minute default, preventing
two consecutive long attempts from silently doubling the runtime.

The fast profile runs one global BA round and skips GLOMAP's final iterative
retriangulation. On ALBAGNAC, a second BA round cost about 5.4 minutes and the
retriangulation exceeded 4 minutes after the model was already globally
optimized. Use `--global-ba-iterations 2 --global-retriangulation` for an
offline high-quality comparison when the one-hour target does not apply.

The provided ALBAGNAC MRK files report median horizontal uncertainties around
1.5–1.6 m and vertical uncertainties around 2.5–2.7 m, so the uncorrected
dataset must be treated as standard GNSS. Use `--gps-quality rtk` only after a
PPK/RTK correction produces centimetric coordinates.

For a corrected mission, `--gps-quality rtk` automatically adds one bounded
`pose_prior_mapper` pass after the fast global reconstruction. DroneAI replaces
the EXIF priors in the COLMAP database with the per-image DJI MRK latitude,
longitude, ellipsoidal height and covariance. The covariance is expressed in
local Cartesian ENU axes (east, north, up), a Cauchy loss limits bad priors, and
the verified GLOMAP/Caspar model is retained if the RTK pass is unavailable or
exceeds its independent timeout. COLMAP currently implements pose-prior BA with
Ceres, not Caspar; GPU solving, one refinement and a hard time budget keep this
last pass bounded.

For a quick smoke test, add `--max-images 80 --selection contiguous`. Use the
full image set for the final speed and registration benchmark.

### Measured full-dataset result

The fast profile was validated on both complete ALBAGNAC flights (1,992 Mavic
3E images, 25.15 GB) with an RTX 4070 Laptop GPU:

| Stage | Measured wall time |
|---|---:|
| Metadata/MRK preflight | about 2 min 30 s |
| Parallel copy from the Windows `Y:` mount | 11 min 36 s |
| SIFT CUDA extraction at 1600 px | 19 min 08 s |
| Brute-force matching of 42,666 bounded pairs | 1 min 22 s |
| GLOMAP, one BA round, color extraction and model write | 10 min 17 s |
| GPS alignment, PLY export and metrics | under 10 s |

The measured components represent about 45 minutes from a cold source mount to
a georeferenced model. The Windows/network copy is almost 26% of that time; a
Linux-local dataset or a warm workspace avoids it. Mapping alone reconstructed
all 1,992 cameras in 527.2 seconds and completed color extraction/model writing
in 617 seconds.

The resulting model registered 1,992/1,992 images, contains 336,531 sparse
points and 2,559,516 observations, and has a mean track length of 7.61. At the
1600 px working resolution, the mean and median point reprojection errors are
3.08 px and 2.22 px. MRK alignment produced a 1.37 m median horizontal camera
residual and a 2.18 m horizontal P95, consistent with the approximately 1.53 m
median uncertainty reported by these non-RTK MRK records.

Use the fast profile for the operational one-hour target. For a controlled
quality comparison, preserve this model and run a separate workspace at
2400 px with two BA rounds and retriangulation; judge the benefit using surveyed
checkpoints rather than the input GNSS residual alone.

### Measured SAVERES RTK result

SAVERES contains 1,066 Mavic 3E images and 1,066 DJI MRK records with complete
centimetric uncertainties. The preflight selected `EPSG:3943` (RGF93 / CC43)
and classified every height as ellipsoidal. On the same RTX 4070 Laptop GPU,
the cold preparation run registered 1,066/1,066 images:

| Stage | Measured wall time |
|---|---:|
| SIFT CUDA extraction at 1600 px | 10 min 28 s |
| Matching of 15,560 bounded pairs | 28.1 s |
| GLOMAP including one global BA pass | 3 min 11 s |
| Undistortion of all images at 1600 px | 15 min 30 s |
| Sum of measured COLMAP commands before RTK | 29 min 40 s |
| Covariance-aware RTK refinement | 25.4 s |

Preflight and the cold 15.20 GB copy add roughly 11 minutes; the complete cold
preparation remains about 41 minutes, below the one-hour target. A warm
workspace avoids that copy.

The RTK pass improved both geometric and absolute-pose diagnostics:

| Metric | Fast global model | RTK-refined model |
|---|---:|---:|
| Registered images | 1,066 | 1,066 |
| Median point reprojection error | 2.612 px | 2.009 px |
| Median 3D GPS residual | 0.343 m | 0.106 m |
| P95 3D GPS residual | 0.750 m | 0.210 m |
| Maximum 3D GPS residual | 9.602 m | 0.341 m |

The exact machine-readable observation is
`docs/benchmarks/saleres-alignment-rtk-2026-07-28.json`. These are camera-prior
residuals, not independent checkpoint accuracy; production acceptance still
requires surveyed GCP/checkpoint RMSE.

## Projected CRS

Metric alignment no longer derives a UTM zone from the first image. The
complete camera footprint is inspected before one CRS is selected:

- `auto-local` uses an audited low-distortion national engineering CRS when
  the complete footprint can be assigned safely, and otherwise falls back to
  the centroid UTM zone;
- metropolitan France uses RGF93 CC42 through CC50 for a local mission and
  Lambert-93 (`EPSG:2154`) when no single CC zone covers the footprint;
- `france-cc` makes the French policy explicit and rejects coordinates outside
  metropolitan France;
- `utm` preserves the historical behavior;
- `custom` accepts an explicit `EPSG:<code>` for the official projected CRS of
  another country or a contractual deliverable.

The interoperable CC9 codes `EPSG:3942` through `EPSG:3950` remain the automatic
default. A survey explicitly delivered in the current RGF93 v2b realization can
select `EPSG:9842` through `EPSG:9850` with `custom`; DroneAI must not infer a
datum realization or coordinate epoch that is absent from DJI EXIF/MRK metadata.

The effective CRS is persisted in `geo_data.txt.crs`; its policy and requested
value are recorded in `geo_data.txt.crs.json`. Changing the policy invalidates
only stale georeferencing products, not the feature database or sparse model.

This reduces planar scale distortion and improves interoperability with
survey/cadastral deliverables. It does not improve the source GNSS observations
or solve the vertical datum: EXIF/MRK height remains a separate ellipsoidal
versus orthometric-reference concern.

## Outputs

The local workspace contains:

- `pair_graph.json`: pair count and graph degree statistics;
- `pairs.txt`: exact pairs submitted to COLMAP;
- `command_timings.json`: wall time of every COLMAP command;
- `metrics.json`: registration, reprojection and GPS residual metrics;
- `model_analyzer.txt`: native COLMAP model summary;
- `sparse/0`: reconstruction used by the downstream DroneAI pipeline;
- `sparse_rtk`: covariance-aware pose-prior refinement when RTK is available;
- `rtk_prior_report.json`: RTK coverage, uncertainty, solver and fallback data;
- `sparse_geo`: georeferenced reconstruction when `--stage align` is used.

## Important camera constraint

Caspar in COLMAP 4.1.1 supports `PINHOLE` and `SIMPLE_RADIAL`. Selecting
`OPENCV` keeps GLOMAP available, but the automatic incremental fallback uses
Ceres CUDA instead of Caspar. DroneAI checks the camera table before starting
Caspar so unsupported observations cannot be silently skipped.

`SIMPLE_RADIAL` is the fast default for the fixed Mavic 3E camera. Before
production promotion, compare it against an `OPENCV` reference run using
independent surveyed checkpoints and image-edge residuals.
