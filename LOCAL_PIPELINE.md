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

## Unified local orchestrator

`run_local_pipeline.sh` is the preferred entry point when the goal is to run
the whole chain. It delegates to the existing COLMAP, Gaussian, and detection
runners; it does not duplicate their scientific logic.

```bash
./tools/run_local_pipeline.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --profile standard
```

Profiles:

- `smoke`: 25 contiguous images, sequential matching, Gaussian `smoke`,
  one-tile YOLO smoke detection
- `standard`: all readable images, spatial matching, Gaussian `low-memory`,
  full YOLO detection

Resume and control examples:

```bash
# Show what would run without changing the workspace
./tools/run_local_pipeline.sh DATASET WORKSPACE --profile standard --dry-run

# Re-run Gaussian and automatically invalidate/re-run detection
./tools/run_local_pipeline.sh DATASET WORKSPACE \
  --profile standard \
  --from-stage gaussian \
  --force-stage gaussian

# Run only the final stage when its prerequisites already exist
./tools/run_local_pipeline.sh DATASET WORKSPACE \
  --profile standard \
  --from-stage detection
```

Completion is based on artifact validation, not merely on the previous process
exit code. The global `pipeline_run.json` records commands, timings, skips,
validation evidence, errors, and stage log paths. Before COLMAP creates the
workspace safety marker, the running manifest stays beside the workspace as a
hidden sidecar; it is moved inside after the marker exists.

Forcing an upstream stage also forces every selected downstream stage so that
an old orthomosaic or detection output cannot be silently reused.

## Prerequisites

- Python 3.11 or 3.12 for the lightweight orchestrator
- Docker with NVIDIA Container Toolkit for GPU execution
- `droneai-api:local` for EXIF/dataset preflight
- `drone-colmap:latest` for reconstruction
- `droneai-gaussian-local:latest` for Gaussian generation
- `drone-ia:latest` for YOLO detection

Build the lightweight image from the repository root:

```bash
docker build \
  --file app4-dashboard/api/Dockerfile \
  --tag droneai-api:local \
  .
```

The full orchestrator needs all four images. A restricted `--to-stage` or
`--from-stage` run only needs the images used by the selected stages.

Image names can be overridden with `DRONEAI_PREFLIGHT_IMAGE`,
`DRONEAI_COLMAP_IMAGE`, `DRONEAI_GAUSSIAN_IMAGE`, and
`DRONEAI_IA_IMAGE`.

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

## Gaussian orthophoto with DroneGS

This part uses a dedicated local image containing the portable DroneGS binary
and Python rendering dependencies. It does not install or start Kubernetes,
Kafka, S3, Postgres, or the dashboard.

No external Gaussian trainer source is required:

```bash
./tools/build_local_gaussian_image.sh
```

The portable CUDA build contains runtime-selected cubins for recent NVIDIA
architectures. To include the legacy LichtFeld rollback in the same image:

```bash
bash setup_deps.sh --with-lichtfeld
./tools/build_local_gaussian_image.sh --with-lichtfeld
```

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
| `balanced` | 15,000 | 1,500,000 | 3 | 4 | 1,600 px | 4 | 0.05 m |

The `balanced` preset reproduces the validated dev.45 training recipe:
FastGS structural rasterization, LichtFeld-compatible pruning bounds,
progressive SH3, a 1,000-step topology cooldown, and a 1,000-step finish to
100% MSE gradient. All presets use seed 42 and select DroneGS explicitly.
Pass `--backend lichtfeld` only with an image built using
`--with-lichtfeld`. Every profile writes separate checkpoints, GeoTIFFs,
height maps, and a `gaussian_run.<profile>.json` manifest. Existing profile
outputs are preserved unless `--force` is passed.

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
