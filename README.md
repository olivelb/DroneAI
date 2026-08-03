# DroneAI Pipeline

DroneAI turns drone imagery into orthophotos, height products and searchable
geospatial detections. It combines photogrammetric reconstruction, 3D Gaussian
Splatting, GPU raster generation, AI inference and an operator dashboard in one
pipeline.

> [!IMPORTANT]
> The current production baseline is designed for one organization behind TLS.
> Multi-tenant isolation, identity federation, exactly-once delivery and high
> availability are outside its present scope.

## Pipeline at a glance

1. **Ingest** drone images and mission settings from the dashboard or local
   runner.
2. **Reconstruct** and geo-align the scene with COLMAP, GLOMAP or Caspar using
   GNSS/RTK data and optional surveyed control.
3. **Train** a qualified DroneGS 3D Gaussian Splatting model, then render a
   georeferenced orthomosaic and height map.
4. **Split** the orthomosaic into overlapping tiles for parallel processing.
5. **Analyse** each tile with YOLO OBB or SAM 3.
6. **Merge** overlapping detections and publish GeoJSON or PostGIS vectors.
7. **Explore** maps, detections, measurements and exports in the dashboard.

## Ways to run DroneAI

| Mode | Intended use | Entry point |
|---|---|---|
| Local dashboard | Complete workstation deployment with Docker Compose | `./deploy.sh local` |
| Distributed dashboard | Single-node K3s deployment managed by Helm | `./deploy.sh distributed` |
| Local runner | Infrastructure-free scientific diagnostics | `./tools/run_local_pipeline.sh` |

DroneAI uses S3-compatible object storage for datasets and mission artifacts,
Kafka for pipeline events, and PostgreSQL/PostGIS for mission and vector data.

## How the parts work together

### Dashboard and API — `app4-dashboard`

The Next.js frontend lets operators upload datasets, configure and launch
missions, follow progress, inspect map layers and export results. Its FastAPI
backend validates requests, stores mission state, publishes work to Kafka and
serves datasets and results from S3-compatible storage and PostGIS.

### Reconstruction and orthomosaic — `app1-colmap`

The reconstruction worker downloads the mission images, extracts and matches
features, builds the sparse scene and aligns it to the requested geographic
frame. It then trains DroneGS, checks the model against the configured quality
gates, renders the orthomosaic and height map, and uploads the products for the
next stage.

### Raster processing — `app3-processing`

The processing worker converts the orthomosaic into overlapping tiles and
queues them for inference. When detections return, it removes duplicates across
tile boundaries, creates the final GeoJSON result and can persist indexed
vectors in PostGIS for spatial search.

### AI inference — `app2-ia`

The AI worker consumes tile jobs and runs either Ultralytics YOLO OBB for
oriented detections or Meta SAM 3 for segmentation. It sends tile-level
geometries and confidence data back to the processing worker for aggregation.

### Shared services — `shared`

Shared modules define configuration, event contracts, validation, storage and
database helpers used by every service. MinIO or another S3-compatible store
holds large artifacts, Kafka carries asynchronous work and status events, and
PostgreSQL/PostGIS holds durable application and vector data.

### Local tools — `tools`

The local runners execute focused reconstruction, Gaussian training,
orthomosaic or full-pipeline diagnostics without the dashboard infrastructure.
They are intended for development and scientific validation rather than normal
operator use.

## Quick start

The recommended workstation setup is:

```bash
git clone https://github.com/olivelb/DroneAI.git
cd DroneAI
./deploy.sh local
```

For the distributed K3s deployment:

```bash
./deploy.sh distributed
```

The deployment command prepares pinned external sources, builds the services,
starts the required infrastructure and prints the dashboard URL. `HF_TOKEN` is
optional for YOLO and required only for gated Hugging Face models such as SAM 3.

## Documentation

The README is intentionally limited to the project overview. Use the dedicated
guides for implementation and operational details:

| Topic | Guide |
|---|---|
| Architecture, event contracts and processing | [`DOCUMENTATION.md`](DOCUMENTATION.md) |
| Local and distributed installation | [`DEPLOYMENT.md`](DEPLOYMENT.md) |
| Infrastructure-free workflow | [`LOCAL_PIPELINE.md`](LOCAL_PIPELINE.md) |
| Development, tests and dependency locks | [`DEVELOPMENT.md`](DEVELOPMENT.md) |
| Production boundary and release gates | [`docs/PRODUCTION_READINESS.md`](docs/PRODUCTION_READINESS.md) |
| Reconstruction and RTK alignment | [`docs/FAST_ALIGNMENT.md`](docs/FAST_ALIGNMENT.md) |
| Map workspace, measurements and exports | [`docs/GEOSPATIAL_WORKSPACE.md`](docs/GEOSPATIAL_WORKSPACE.md) |
| Validation reports and benchmarks | [`docs/`](docs/) |
| Third-party components and licenses | [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) |

## Showcase

![Vehicle detection on orthomosaic](docs/showcase_vehicle_detection.png)

The image shows georeferenced vehicle detections reprojected onto an
orthomosaic. The measured local, non-RTK validation is documented in
[`docs/GAJAN_R2S_VALIDATION.md`](docs/GAJAN_R2S_VALIDATION.md).

## License

DroneAI source code is licensed under the [MIT License](LICENSE). External
source trees, container images, model code and model weights retain their own
licenses; review [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) before
redistribution.
