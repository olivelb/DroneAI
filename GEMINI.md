# Drone Imagery & AI Processing Pipeline

A specialized, microservices-based pipeline for processing drone photography, generating real 2D orthomosaics from 3D point clouds, and performing AI-based object detection with geographic referencing.

## Project Overview

The project consists of four main microservices that communicate asynchronously via an Apache Kafka message broker. The entire stack is containerized and managed via Kubernetes (k3s).

### 1. App 1 (COLMAP Worker)
*   **Purpose:** Listens for raw drone image missions on the `vols-bruts` topic. Supports pipeline cancellation via the `pipeline-control` topic.
*   **Logic:** Uses **COLMAP 4 (CUDA-accelerated)** with a **dual-pipeline architecture**:
    *   **Modern pipeline** (default): ALIKED N16ROT features + LightGlue matching, GLOMAP global mapper, view-graph calibration, Poisson mesh → texture → rasterized orthomosaic.
    *   **Legacy pipeline** (`"pipeline": "legacy"`): SIFT features + brute-force matching, incremental mapper, PLY point cloud projection. Backward-compatible with COLMAP 3.x databases.
    *   `detect_existing_pipeline()` auto-detects the descriptor type in `database.db` and purges on mismatch.
    *   Resume support: PatchMatchStereo detects existing depth maps and skips completed work.
*   **Output:** Publishes completion status and the path of the generated `.tif` to the `images-ortho` topic.

### 2. App 2 (IA Worker)
*   **Purpose:** Listens for processed image tiles to perform object detection.
*   **Logic:** Employs **YOLOv11 segmentation** (`yolo11n-seg.pt` via `ultralytics`) for AI object detection (e.g., cars, pylons). Uses GPU acceleration for inference. Model is pre-downloaded at build time.
*   **Output:** Publishes geo-referenced detections to the `tile-detections` topic.

### 3. App 3 (Processing Worker)
*   **Purpose:** Acts as the middleman between the Orthomosaic generation and the IA detection.
*   **Logic:** Listens on `images-ortho`, takes the large `orthomosaic.tif`, and slices it into manageable tiles using **Rasterio** and GDAL. It manages mission state and aggregates detection results, ensuring large maps do not overwhelm the AI worker. Tile output directory is configurable via `TILES_BASE_DIR` env var.

### 4. App 4 (Dashboard & API)
*   **Frontend:** A **Next.js** web interface (`http://localhost:30000`) for browsing local datasets, starting missions, and monitoring real-time logs and progress. Branded as **DroneAI Control Center**.
*   **Backend:** A **FastAPI** service (`http://localhost:30080`) using `lifespan` context manager. Bridges the React frontend and Kafka with real-time WebSocket updates. Includes per-connection error handling for robust broadcasting.

## Kafka Topics

| Topic | Producer → Consumer | Purpose |
|---|---|---|
| `vols-bruts` | API → App 1 | Mission dispatch (raw drone images) |
| `images-ortho` | App 1 → App 3 | Orthomosaic ready notification |
| `image-tiles` | App 3 → App 2 | Individual tile dispatch for AI inference |
| `tile-detections` | App 2 → App 3 | Detection results per tile |
| `pipeline-control` | API → App 1 | Cancel commands |
| `pipeline-status` | All → API → UI | Progress/logs (WebSocket broadcast) |

## Main Technologies

*   **Languages:** Python 3.11, TypeScript (React/Next.js)
*   **AI/ML Frameworks:** PyTorch, Ultralytics (YOLOv11)
*   **GIS Libraries:** Rasterio, PyProj, GDAL
*   **Photogrammetry:** COLMAP 4 (dual-pipeline: ALIKED/LightGlue/GLOMAP + legacy SIFT), Plyfile, Trimesh
*   **Messaging:** Apache Kafka (Confluent Kafka Python client)
*   **Infrastructure:** Docker, Kubernetes (k3s), NVIDIA Device Plugin

## Infrastructure & Configuration

-   **Kafka Broker:** Configured via `kafka-local.yaml` and accessible inside the cluster at `my-kafka.kafka.svc.cluster.local:9092`.
-   **GPU Acceleration:** Required for both COLMAP (CUDA) and YOLO inference. Targeted for NVIDIA RTX 3090 (24GB VRAM). GPU resource limits (`nvidia.com/gpu: 1`) are configured on COLMAP and IA worker deployments. The `Dockerfile`s are based on PyTorch/CUDA and COLMAP native images.
-   **Volumes:** The host filesystem is mounted to `/host` within the containers to allow seamless reading of raw datasets and writing to the `workspace/` directory.

## Building and Running

### Prerequisites
- NVIDIA GPU with CUDA drivers installed.
- `k3s` (Kubernetes) and Docker installed.

### Deployment Commands

To completely rebuild and deploy the entire microservices stack, run the deployment script from the project root:

```bash
sudo ./build_and_deploy.sh
```

This script will:
1. Build the 5 Docker images (`colmap`, `ia`, `processing`, `api`, `frontend`).
2. Export and import them into the `k3s` containerd registry.
3. Apply the `kafka-local.yaml` manifests to update the Kubernetes Deployments and Services.
4. Restart the deployments to immediately apply code changes.

### Accessing the System
- **Web UI:** [http://localhost:30000](http://localhost:30000)
- **API:** [http://localhost:30080](http://localhost:30080)

## Directory Structure

*   `app1-colmap/`: Photogrammetry worker (Python/COLMAP).
*   `app2-ia/`: Object detection worker (Python/YOLO). Includes `.dockerignore` to exclude `.pt` files from builds.
*   `app3-processing/`: Image tiling and aggregation worker (Python/Rasterio).
*   `app4-dashboard/`: Next.js UI (`frontend/`) and FastAPI backend (`api/`).
*   `workspace/`: The main persistent storage directory where databases, `.ply` files, and `.tif` maps are generated per mission.
*   `build_and_deploy.sh`: Primary build and orchestration script.
*   `kafka-local.yaml`: Kubernetes manifests for the entire stack.

## Recent Architectural Updates & Fixes

### COLMAP 4 Migration (Dual-Pipeline)
- **Modern pipeline** uses ALIKED N16ROT features, LightGlue matcher, GLOMAP global mapper, and Poisson mesh-based orthomosaic generation (via Trimesh). Selected by default for new missions.
- **Legacy pipeline** preserves SIFT + incremental mapper behavior for backward compatibility with in-progress COLMAP 3.x missions. Activated with `"pipeline": "legacy"` in the mission message.
- **Auto-detection** of existing database descriptor type prevents pipeline mismatches.
- **UI pipeline selector** in the dashboard allows choosing between Modern and Legacy before starting a mission.

### Bug Fixes (14 fixes across 8 files)
- **Race condition fix (App 3):** `total_tiles` is now set before producing any tile messages. Detection handler uses `.get('total_tiles')` with a `None` guard.
- **GPS hemisphere fix (App 1):** Reads `gps_latitude_ref`/`gps_longitude_ref` and negates for S/W hemispheres.
- **WebSocket leak fix (Frontend):** Split into two `useEffect` hooks — browsing (`[currentPath]`) and WebSocket (`[]`) — to prevent reconnection on every navigation.
- **WebSocket disconnect crash fix (API):** `list.remove()` wrapped in `try/except ValueError`.
- **Broadcast failure fix (API):** Per-connection error handling; dead connections are pruned instead of killing the broadcast loop.
- **Deprecated startup handler (API):** Replaced `@app.on_event("startup")` with `lifespan` context manager.
- **Dummy ortho origin fix (App 1):** Exception fallback uses last known `min_x`/`max_y` from point cloud instead of `(0, 0)`.
- **Scope check fix (App 1):** Replaced fragile `'utm_crs' in locals()` with direct `if utm_crs`.
- **Hardcoded path fix (App 3):** `tiles_base` reads from `TILES_BASE_DIR` env var.

### Infrastructure
- **GPU resource limits** added to `colmap-worker` and `ia-worker` K8s deployments (`nvidia.com/gpu: 1`).
- **Vestigial `yolo11n.pt` removed** from `app2-ia/` (~5.6MB saved per build). `.dockerignore` excludes `*.pt` files.
- **Frontend metadata** updated from "Create Next App" to "DroneAI Control Center".

### Earlier Updates
- **Real Orthomosaic Generation:** App 1 actively parses the MVS `fused.ply` point cloud, calculates spatial boundaries, and writes a true scaled TIFF. Empty point-cloud edge cases are safely caught.
- **Optimized COLMAP Parameters:** MVS filtering and extraction limits optimized to utilize 24GB VRAM, allowing small datasets to fuse successfully.
- **WebSocket Synchronization:** The Next.js dashboard correctly multiplexes pipeline progress bars and engine logs simultaneously.
- **Path Resolution:** File browsing directly targets the native `/host/home/...` paths, eliminating empty UI states.

## Known Limitations & Roadmap

- No dead-letter queue or retry logic for Kafka consumers.
- In-memory mission state in App 3 is not persistent (lost on pod restart).
- No liveness/readiness probes on K8s deployments.
- Full host filesystem mounted as `/host` (security concern).
- Cancel support only in App 1 (not App 2 or App 3).
- Results tab in dashboard is a placeholder (Leaflet/MapLibre map visualization planned).