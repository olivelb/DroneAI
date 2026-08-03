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

1. **Reconstruct** the scene from drone images with COLMAP, GLOMAP or Caspar.
2. **Align** aerial missions from GNSS/RTK data and optional surveyed control.
3. **Train** a qualified DroneGS 3D Gaussian Splatting model.
4. **Render** a georeferenced aerial orthomosaic and height map, or a facade
   orthophoto in a local coordinate frame.
5. **Analyse** overlapping raster tiles with YOLO OBB or SAM 3.
6. **Publish** deduplicated detections as GeoJSON and optional PostGIS vectors.
7. **Operate** missions, results and exports from the Next.js dashboard.

## Ways to run DroneAI

| Mode | Intended use | Entry point |
|---|---|---|
| Local dashboard | Complete workstation deployment with Docker Compose | `./deploy.sh local` |
| Distributed dashboard | Single-node K3s deployment managed by Helm | `./deploy.sh distributed` |
| Local runner | Infrastructure-free scientific diagnostics | `./tools/run_local_pipeline.sh` |

DroneAI uses S3-compatible object storage for datasets and mission artifacts,
Kafka for pipeline events, and PostgreSQL/PostGIS for mission and vector data.

## Repository map

| Directory | Responsibility |
|---|---|
| `app1-colmap` | Reconstruction, alignment, Gaussian training and raster generation |
| `app2-ia` | YOLO OBB and SAM 3 inference |
| `app3-processing` | Tiling, recovery, deduplication and vector publication |
| `app4-dashboard/api` | FastAPI control plane, storage API and mission state |
| `app4-dashboard/frontend` | Next.js operator dashboard |
| `shared` | Shared configuration, contracts, persistence and validation |
| `tools` | Local runners and diagnostic utilities |

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
