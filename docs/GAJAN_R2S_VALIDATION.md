# GAJAN R2S local validation

Validation date: 2026-07-23

This case study exercises the infrastructure-free workflow from
[`LOCAL_PIPELINE.md`](../LOCAL_PIPELINE.md). The source photographs and
generated workspaces are not committed to the repository.

## Dataset

| Property | Result |
|---|---:|
| Images | 111 JPEG |
| Total size | 943,061,003 bytes |
| Camera | DJI FC3411 |
| Dimensions | 5472 × 3078 |
| GPS coverage | 111/111 (100%) |
| Capture interval | 2024-10-30 15:40:17–15:53:32 |
| EXIF altitude | 357.0–361.6 m |
| Approximate flight path | 1,064.4 m |
| Recommended CRS | EPSG:32631 |

Positioning is standard DJI GNSS, without RTK or surveyed ground control
points. The source disk was mounted read-only and images were copied into
separate WSL workspaces.

## Commands

The smoke test used 25 contiguous images, sequential matching, SIFT features at
2400 px, and a 10 m robust GPS alignment threshold:

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-smoke" \
  --stage align \
  --max-images 25 \
  --selection contiguous \
  --matcher sequential \
  --feature-max-image-size 2400 \
  --alignment-max-error 10
```

The full run used all images, spatial matching, and SIFT features at 3200 px:

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --stage align \
  --matcher spatial \
  --feature-max-image-size 3200 \
  --alignment-max-error 10
```

## Results

| Metric | 25-image smoke test | 111-image full run |
|---|---:|---:|
| Registered images | 25/25 (100%) | 111/111 (100%) |
| Sparse points | 9,324 | 53,203 |
| Mean reprojection error | 1.01 px | 1.27 px |
| Median reprojection error | 0.96 px | 1.27 px |
| Horizontal GPS residual, median | 1.07 m | 0.94 m |
| Horizontal GPS residual, P95 | 1.72 m | 1.81 m |
| Vertical GPS residual, median | 0.15 m | 0.45 m |
| 3D GPS residual, median | 1.09 m | 1.04 m |
| 3D GPS residual, P95 | 1.72 m | 1.94 m |
| Maximum 3D GPS residual | 1.79 m | 2.58 m |

The full sparse model also contains:

- 366,818 observations;
- mean track length of 6.89;
- mean 3,304.67 observations per image;
- one shared OPENCV camera model.

The generated full workspace occupies approximately 1.2 GiB, including a
206 MiB COLMAP database and a 780 KiB georeferenced sparse PLY export.

## Interpretation

The dataset has strong visual overlap and is internally consistent: all images
register and the sparse reconstruction has healthy track lengths. A 10 m
alignment threshold accommodates standard GNSS safely, while the observed P95
horizontal residual remains below 2 m.

These are alignment residuals against the same onboard GNSS positions used to
fit the Sim3 transform. They demonstrate consistency, not independent absolute
accuracy. Without RTK, PPK, or surveyed control points, the result must not be
presented as centimetric. EXIF altitude also lacks an independently verified
vertical datum.

The next technically meaningful experiment is image undistortion followed by a
reduced-memory Gaussian Splatting profile for the available 8 GiB GPU. It
should remain a separate step so the validated sparse baseline stays
reproducible.
