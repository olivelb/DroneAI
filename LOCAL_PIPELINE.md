# Local photogrammetry workflow

This workflow validates image metadata, sparse reconstruction, GPS alignment,
image undistortion, and an optional Gaussian orthophoto without Kafka, S3,
Postgres, Kubernetes, or the dashboard. Source images are mounted read-only
and copied into a marked local workspace before COLMAP processes them.

See [`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md) for a real
111-image, non-RTK validation run and its measured results.

## Scope

The COLMAP runner deliberately stops before Gaussian Splatting. This keeps the
geometric baseline cheap and reproducible. A separate Gaussian runner can then
reuse that workspace without starting any service infrastructure.

- Are the images readable and consistently tagged?
- Is there enough visual overlap for a sparse reconstruction?
- How many images register successfully?
- What are the point reprojection errors?
- Is standard drone GNSS sufficient for approximate model alignment?

## Prerequisites

- Docker with NVIDIA Container Toolkit for GPU execution
- a built `drone-colmap:latest` image
- a lightweight API image containing Pillow

Build the lightweight image from the repository root:

```bash
docker build \
  --file app4-dashboard/api/Dockerfile \
  --tag droneai-api:local \
  .
```

The image names can be overridden with `DRONEAI_COLMAP_IMAGE` and
`DRONEAI_PREFLIGHT_IMAGE`.

## Accessing a Windows dataset from WSL

For example, mount `D:` read-only:

```bash
sudo mkdir -p /mnt/d
sudo mount -t drvfs -o ro D: /mnt/d
```

The Docker wrapper also mounts the source dataset read-only. COLMAP operates on
copies under the workspace, never on the source photographs.

## Dataset preflight

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s" \
  --stage preflight
```

Outputs:

- `dataset_preflight.json`: per-image EXIF and aggregate quality metrics
- `flight_path.geojson`: camera locations and approximate acquisition path

## Sparse smoke test

Start with a contiguous sequence to validate camera configuration and visual
overlap quickly:

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

The legacy SIFT path is used intentionally. The current COLMAP image does not
include the ONNX Runtime support required by ALIKED and LightGlue.

## Full sparse reconstruction

After a successful smoke test:

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --stage align \
  --matcher spatial \
  --feature-max-image-size 3200 \
  --alignment-max-error 10
```

Use `--stage undistort` after validating the sparse model if undistorted images
are required for a later Gaussian Splatting experiment.

CPU execution is available with `--no-use-gpu`, although it is substantially
slower.

## Workspace safety and resumability

The runner refuses to modify a non-empty directory unless it contains its
`.droneai-local-workspace.json` marker. It resumes existing COLMAP artifacts
when the selected source images have not changed. `--force` removes only known
generated artifacts inside a marked workspace.

Key outputs are:

- `database.db`
- `sparse/`
- `sparse_geo/`
- `alignment_transform.json`
- `sparse.ply` or `sparse_geo.ply`
- `geo_data.txt` and `geo_data.txt.crs`
- `model_analyzer.txt`
- `metrics.json`

## Interpreting non-RTK results

Standard DJI GNSS is useful for scale, orientation, approximate placement, and
robust Sim3 alignment. It is not centimetric ground truth. Evaluate:

- image registration percentage;
- point reprojection errors for internal geometric consistency;
- horizontal and vertical GPS residuals separately;
- median and P95 residuals rather than only the maximum.

The default alignment tolerance is 10 m. Tighten it only after observing the
dataset residual distribution. Absolute accuracy requires RTK/PPK or surveyed
ground control points, and altitude tags may use a vertical reference that
differs from the target GIS product.

## Optional Gaussian orthophoto

This part needs one additional local image containing the pinned, patched
LichtFeld binary and the Python rendering dependencies. It does not install or
start Kubernetes, Kafka, S3, Postgres, RabbitMQ, or the dashboard.

Only LichtFeld and its C++ package manager are needed to build it:

```bash
git clone --filter=blob:none \
  https://github.com/MrNeRF/LichtFeld-Studio.git LichtFeld-Studio
git -C LichtFeld-Studio checkout 1004c0841a3776e3f67866ff34101fbc9677397f
git -C LichtFeld-Studio apply \
  ../app1-colmap/patches/lichtfeld-pipeline-minimal.patch

git clone --branch 2026.03.18 \
  https://github.com/microsoft/vcpkg.git .docker-vcpkg

./tools/build_local_gaussian_image.sh
```

The build inputs are ignored by Git. The final image contains the
pipeline-minimal headless trainer, not the full graphical LichtFeld
application.

First undistort the small workspace, then run the complete smoke path:

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-smoke" \
  --stage undistort \
  --max-images 25 \
  --selection contiguous \
  --matcher sequential \
  --feature-max-image-size 2400 \
  --alignment-max-error 10

./tools/run_local_gaussian.sh \
  "$HOME/droneAI-workspaces/gajan-r2s-smoke" \
  --profile smoke
```

Once that succeeds, the conservative RTX 4070 Laptop / 8 GiB profile is:

```bash
./tools/run_local_gaussian.sh \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --profile low-memory
```

| Profile | Iterations | Gaussian cap | SH | Image factor | Max dimension | Tile mode | GSD |
|---|---:|---:|---:|---:|---:|---:|---:|
| `smoke` | 500 | 100,000 | 0 | 8 | 1,024 px | 4 | 0.25 m |
| `low-memory` | 5,000 | 500,000 | 1 | 4 | 1,600 px | 4 | 0.10 m |
| `balanced` | 15,000 | 1,500,000 | 2 | 2 | 2,400 px | 4 | 0.05 m |

These profiles are validation presets, not claims of optimal quality. The
runner forwards image scaling as LichtFeld's actual `--resize_factor` option
and enables its memory-saving tile mode. Every profile writes separate
checkpoints, GeoTIFFs, height maps, and a `gaussian_run.<profile>.json`
manifest. Existing profile outputs are preserved unless `--force` is passed.

## Optional local YOLO OBB detection

The detector can consume a generated orthomosaic without Kafka, MinIO,
Postgres, K3s, or the dashboard. It reuses the production YOLO extraction and
processing-worker deduplication code.

Prerequisites:

- the existing `drone-ia:latest` image;
- NVIDIA Container Toolkit;
- a marked local workspace containing a georeferenced orthomosaic.

Run one tile first:

```bash
./tools/run_local_detection.sh \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --profile smoke
```

Then run every overlapping tile with the larger aerial OBB model:

```bash
./tools/run_local_detection.sh \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --profile full
```

| Profile | Model | Tile size | Overlap | Confidence | Tile limit |
|---|---|---:|---:|---:|---:|
| `smoke` | YOLO26n OBB | 1,024 px | 256 px | 0.20 | 1 |
| `full` | YOLO26l OBB | 1,024 px | 256 px | 0.20 | all |

The wrapper mounts the repository read-only, the marked workspace read-write,
and a persistent model cache from
`$HOME/.cache/droneai/models`. `DRONEAI_IA_IMAGE` and
`DRONEAI_MODEL_CACHE` can override those defaults.

Each profile writes under `detection_runs/<profile>/`:

- `detection_run.json`: parameters, timings, per-tile attempts, and summary;
- `detections.raw.json`: tile-level observations before overlap merging;
- `detections.json`: deduplicated objects with pixel, projected, and GPS centers;
- `detections.geojson`: WGS84 OBB polygons;
- `orthomosaic.annotated.tif`: georeferenced annotated RGB output.

Existing outputs are preserved unless `--force` is passed. Custom source and
output paths must remain inside the marked workspace.
