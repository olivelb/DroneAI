# Local photogrammetry workflow

This workflow validates image metadata, sparse reconstruction, GPS alignment,
and image undistortion without Kafka, S3, Postgres, Kubernetes, or the
dashboard. Source images are mounted read-only and copied into a marked local
workspace before COLMAP processes them.

See [`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md) for a real
111-image, non-RTK validation run and its measured results.

## Scope

The local runner deliberately stops before Gaussian Splatting, orthomosaic
rendering, tiling, and object detection. It is intended to answer these
questions first:

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
