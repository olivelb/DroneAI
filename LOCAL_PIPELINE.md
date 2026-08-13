# Infrastructure-free photogrammetry workflow

> [!NOTE]
> This is the advanced dashboard-free diagnostic runner. For a complete local
> dashboard, Kafka, storage, database and all workers from a fresh clone, use
> `./deploy.sh local` as documented in [`DEPLOYMENT.md`](DEPLOYMENT.md).

This workflow validates image metadata, sparse reconstruction, GPS alignment,
image undistortion, and an optional Gaussian orthophoto without Kafka, S3,
Postgres, Kubernetes, or the dashboard. Source images are mounted read-only
and copied into a marked local workspace before COLMAP processes them.

See [`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md) for a real
111-image, non-RTK validation run and its measured results.

See [`docs/FAST_ALIGNMENT.md`](docs/FAST_ALIGNMENT.md) for the COLMAP 4.1.1
GLOMAP/Caspar path, two-flight ALBAGNAC command and complete SAVERES RTK
measurement.

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
  one-tile YOLO smoke detection; this only checks integration and is not a
  user-facing quality profile
- `fast`: all readable images with the versioned `fast-v1` envelope: SIFT CUDA
  at 1600 px and 2048 features, 7500 iterations, 1.5M Gaussian cap, image
  factor 8, and full YOLO detection
- `normal`: all readable images with the versioned `normal-v2` envelope: SIFT
  CUDA at 2400 px and 4096 features, 15,000 iterations, adaptive Gaussian
  capacity from 3M up to 8M according to surface/GSD/VRAM, image factor 4,
  and full YOLO detection
- `high-quality`: all readable images with the versioned `high-quality-v2`
  envelope: SIFT CUDA at 4096 px and 16,384 features, 30,000 iterations,
  adaptive Gaussian capacity from 5M up to 12M according to surface/GSD/VRAM,
  image factor 1, and full detection; this is the reproducible BIGZEN/24 GiB
  qualification path, not a default laptop profile
- `standard`: all readable images, bounded GPS matching, SIFT CUDA at 2400 px
  and 4096 features, two global BA passes with final retriangulation, Gaussian
  `low-memory`, and full YOLO detection

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

- Python 3.12 for the lightweight orchestrator
- Docker with NVIDIA Container Toolkit for GPU execution
- `droneai-api:local` for EXIF/dataset preflight
- `drone-colmap:latest` for reconstruction
- `droneai-gaussian-local:latest` for Gaussian generation
- `drone-ia:latest` for YOLO detection

These `latest` names are deliberate workstation-only aliases used by the local
diagnostic scripts. They are not deployment identities and must never be
copied into a preproduction/production Helm executor map. Local K3s may use a
Git-SHA tag; staging and production require a promoted OCI digest.

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

DJI Enterprise `*_Timestamp.MRK` records override lower-precision EXIF
coordinates when present. `Ellh` is recorded explicitly as an ellipsoidal
height together with its vertical standard deviation. EXIF-only altitude is
marked unknown. DroneAI never labels a height as NGF-IGN69 unless a future
explicit RAF20/Circé transformation is applied.

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

SIFT CUDA is the measured fast default. The image also contains ONNX Runtime
GPU and embedded ALIKED/LightGlue models for explicit comparison runs.

## Full sparse reconstruction

After a successful smoke test:

```bash
./tools/run_local_colmap.sh \
  "/mnt/d/GAJAN/GAJAN R2S" \
  "$HOME/droneAI-workspaces/gajan-r2s-full" \
  --stage align \
  --alignment-max-error 10
```

With no alignment overrides, this command uses the same planimetric survey
profile as the dashboard: GPS pairs, SIFT brute force, `SIMPLE_RADIAL`,
2400 px, 4096 features, two BA passes and final retriangulation.

Use `--stage undistort` after validating the sparse model if undistorted images
are required for a later Gaussian Splatting experiment.

CPU execution is available with `--no-use-gpu`, although it is substantially
slower.

For a corrected DJI RTK/PPK mission with complete MRK uncertainties:

```bash
./tools/run_local_colmap.sh DATASET WORKSPACE \
  --stage undistort \
  --engine auto \
  --matcher gps \
  --gps-quality rtk \
  --projected-crs-mode auto-local \
  --rtk-refinement-iterations 25 \
  --rtk-refinement-timeout-seconds 900
```

The runner requires at least 95% pose-prior coverage before modifying the
database, runs one robust bounded Ceres GPU pass, and retains the verified
GLOMAP/Caspar model if refinement fails or times out.

## HD facade process

`--facade` selects the same qualified coverage-first recipe as the dashboard:
Caspar, SIFT at 4200 px, 16,384 features/matched features, a 48/16 spatial
neighbour graph with six temporal neighbours and a four-hour mapping budget.
It creates no CRS and disables absolute RTK, GCP and gravity fitting.

For the validated Cahors mission, omit the coherent rose-window/portal detail
sequence while keeping every systematic pass:

```bash
./tools/run_local_colmap.sh DATASET WORKSPACE \
  --facade \
  --stage undistort \
  --exclude-image-range \
    DJI_20250324162114_0307_V.JPG \
    DJI_20250324163256_0658_V.JPG

./tools/run_local_gaussian.sh WORKSPACE \
  --render-mode facade \
  --profile facade-hd \
  --facade-scale-mode gps-baseline \
  --resolution 0.01
```

The exclusion is inclusive, must match the mission basenames and is recorded
in `facade_selection_report.json`. It is not a global default: use it only for
this sequence or after a sparse-distribution comparison demonstrates the same
localized-density problem. The `facade-hd` profile runs 30,000 iterations
at up to 4096 px, caps the model at two million Gaussians and gates held-out
views at 18 dB PSNR / 0.25 SSIM. See
[`docs/FACADE_ORTHOPHOTO.md`](docs/FACADE_ORTHOPHOTO.md) for the product and
acceptance contract.

## Workspace safety and resumability

The runner refuses to modify a non-empty directory unless it contains its
`.droneai-local-workspace.json` marker. It resumes existing COLMAP artifacts
when the selected source images have not changed. `--force` removes only known
generated artifacts inside a marked workspace.

Key outputs are:

- `database.db`
- `sparse/`
- `sparse_rtk/` when covariance-aware RTK refinement succeeds
- `sparse_geo/`
- `rtk_prior_report.json`
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

### Weighted GCP adjustment

Place `gcp_list.txt` anywhere below the dataset root. To control influence and
retain independent checks, add `gcp_accuracy.csv`:

```csv
point_id,horizontal_accuracy_m,vertical_accuracy_m,image_accuracy_px,role
GCP01,0.015,0.025,0.5,adjustment
GCP02,0.015,0.025,0.5,checkpoint
```

Enable `gcp_adjustment_enabled` in the Reconstruction phase. Values are 1-sigma
standard deviations, not tolerances: a value two times smaller gives roughly
four times the least-squares information before robust loss. `checkpoint`
points are triangulated and reported in `gcp_alignment_report.json` but never
fit. Without the CSV, mission-level defaults apply and every point is an
adjustment control.

`imu_gravity_enabled` converts complete Autel/DJI gimbal pitch and roll to the
COLMAP camera frame and enables GLOMAP gravity rotation averaging only above
95% coverage. It is disabled by default: Helenenschacht showed no material
accuracy gain. Enable it only for a validated camera pair or a weak/rotated
image graph, and inspect `imu_gravity_report.json`.

## Gaussian orthophoto with DroneGS

This part uses a dedicated local image containing the portable DroneGS binary
and Python rendering dependencies. It does not install or start Kubernetes,
Kafka, S3, Postgres, or the dashboard.

No external Gaussian trainer source is required:

```bash
./tools/build_local_gaussian_image.sh
```

The portable CUDA build contains runtime-selected cubins for recent NVIDIA
architectures. LichtFeld and vcpkg are not cloned or compiled, and no alternate
Gaussian backend is packaged in the image.

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
| `fast` (`fast-v1`) | 7,500 | 1,500,000 | 3 | 8 | 1,600 px | 4 | 0.05 m |
| `normal` (`normal-v2`) | 15,000 | adaptive 3–8 M | 3 | 4 | 2,400 px | 4 | 0.05 m |
| `high-quality` (`high-quality-v2`) | 30,000 | adaptive 5–12 M | 3 | 1 | 4,096 px | 1 | 0.015 m |
| `low-memory` | 5,000 | 500,000 | 1 | 4 | 1,600 px | 4 | 0.10 m |
| `balanced` | 15,000 | 1,500,000 | 3 | 4 | 1,600 px | 4 | 0.05 m |

`smoke` exercises checkpoint/resume and modulo held-out evaluation but uses a
zero-threshold operational canary. `low-memory` uses the spatial-block canary
with a 25% guard ring and gates at 15 dB / 0.10 SSIM; `balanced` preserves the
immutable modulo production baseline at 18 dB / 0.25 SSIM.

The `balanced` preset applies immutable `DRONEGS_PRODUCTION_PROFILE_V1` with
the dev.47 trainer:
FastGS structural rasterization, bounded spatial pruning,
progressive SH3, a 1,000-step topology cooldown, and a 1,000-step finish to
100% MSE gradient. All presets use seed 42 and select DroneGS explicitly.
Every profile writes separate checkpoints, GeoTIFFs, height maps, and a
`gaussian_run.<profile>.json` manifest. Interrupted native training resumes
from `training.ckpt`; completed models must pass the configured held-out
PSNR/SSIM canary before rendering. Existing profile outputs are preserved
unless `--force` is passed.

### Expert overrides and production identity

The local runner accepts the following profile overrides:

| Group | Options |
|---|---|
| Budget | `--iterations`, `--cap-max`, `--sh-degree` |
| Input | `--data-factor`, `--max-width`, `--tile-mode` |
| Optimizer | `--optimizer-profile`, `--pruning-policy`, `--raster-profile` |
| Schedules | `--sh-degree-interval`, `--topology-cooldown`, `--photometric-finish`, `--photometric-mse-percent` |
| Qualification | `--canary-min-psnr`, `--canary-min-ssim` |
| Reproducibility | `--seed` |
| Raster | `--resolution`, `--filter` / `--no-filter` |

Any trainer or qualification override that no longer equals the immutable V1
recipe automatically changes the recorded `profile_id` from
`DRONEGS_PRODUCTION_PROFILE_V1` to `custom`. Raster-only choices such as
`--resolution` do not change the trainer identity. The effective values and
identity are written to `gaussian_run.<profile>.json`.

Canary thresholds are validated (`PSNR >= 0`, `0 <= SSIM <= 1`). Lowering a
threshold is appropriate only for an explicitly labelled diagnostic render:
it does not repair failed quality and does not qualify the result for
production.

The
[Helenenschacht ultra 5 mm report](docs/benchmarks/helenenschacht-dronegs-ultra-5mm-2026-07-30.md)
records a demanding 30,000-step, factor-1 run. It produced a sharper COG but
failed the production SSIM gate and retained a 5,0 cm horizontal GCP RMSE.
Consequently it remains `custom`; it is not a new default.

The final renderer uses compensated 0.03 px² Mip filtering. The diagnostic
tool `tools/benchmark_dronegs_ortho_filter.py` compares it against the legacy,
unfiltered, and 0.1 px² variants on fixed local crops. For Helenenschacht, a
1 cm output matches the approximately 1.02 cm native flight GSD; 5 mm output is
oversampling and cannot recover texture absent from the source photographs.

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
