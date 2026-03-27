# DroneAI Pipeline - Recent Changes & Upgrades

This document summarizes the recent architectural changes, feature additions, and codebase upgrades made to the DroneAI pipeline.

## 1. AI Backend Upgrades: SAM 3 Integration
- **Dual-Backend Support**: The IA worker (`app2-ia`) now supports both **Ultralytics YOLO OBB** and **Meta SAM 3** prompt-based segmentation.
- **Hugging Face Token Auth**: Added support for securely loading the gated `facebook/sam3` model via a Kubernetes secret (`hf-token`).
- **Persistent HF Cache**: The IA worker now mounts a persistent cache volume (`/var/lib/drone-ai/huggingface-cache`) on the host to avoid re-downloading large SAM 3 models between pod restarts.
- **Dynamic Prompting**: The dashboard frontend and API now accept `sam_prompt` in the mission payload, enabling zero-shot detection of specific classes (e.g., "car", "vehicle").

## 2. Robust Orthomosaic Aggregation (`app3-processing`)
- **Overlap Deduplication**: Harden detection aggregation. Overlap duplicates from adjacent tiles are now intelligently merged using:
  - Polygon area-based sorting (largest kept).
  - Centroid and vertex inside-containment checks.
  - Custom IoU-based threshold checks (`UNTILER_DEDUPE_CENTER_THRESHOLD` and `UNTILER_DEDUPE_IOU_THRESHOLD`).
- **Race Condition Fixes**: Fixed asynchronous race conditions where tile detections might return before the total tile count is mapped. `total_tiles` is eagerly published before mapping begins.
- **Mission Isolation**: Tile slices are now saved in a mission-scoped directory (`tiles/<vol_id>/`) rather than a shared workspace folder to prevent cross-mission collisions. 

## 3. YOLO Training & Dataset Utilities
- **Training Resumption Helpers**: `train_eagle_yolo11_obb.py` was explicitly updated with:
  - `--resume` switch with expansive path resolution.
  - `--auto-resume-latest` feature to automatically find and load the most recently modified `last.pt` weight file under the target project directory.
- **Improved Dataset Tiling**: `tile_eagle_obb_dataset.py` creates overlapping tile crops and drops labels crossing boundaries for better EAGLE YOLO OBB model training.

## 4. API & Dashboard Frontend Enhancements (`app4-dashboard`)
- **Production Build Fixes**: Rectified `dashboard-frontend` React/Next.js build errors for smooth containerization.
- **Expanded REST Endpoints**: The dashboard API no longer relies solely on WebSockets for status; it now bridges Kubernetes and host OS diagnostics via:
  - `GET /pods`: Tracks pod health and restarts via Kubernetes ServiceAccount RBAC.
  - `GET /status/summary`: Provides an aggregated list of ongoing and past mission statuses.
  - `GET /system/resources`: Exposes `/proc/meminfo` metrics to the frontend.
  - `POST /mission/estimate`: A pre-flight planner to estimate fusion memory pressure and maximum image size.
  - `GET /mission/parameters`: Fetches pipeline parameter metadata to dynamically render UI config inputs.

## 5. Deployment Infrastructure & Operational Tooling
- **Split Manifests for RBAC**: The Kubernetes deployment configurations have been modularly split into `kafka-local.yaml` (deployments, volumes) and `dashboard-api-rbac.yaml` (ServiceAccount & Pod-reader roles).
- **Targeted Redeployment**: Streamlined `deploy_app*.sh` scripts out of the holistic `build_and_deploy.sh` script to allow incremental hot-reloading of specific microservices.
- **Cleanup Utilities**: Added `cleanup_runtime.sh` to purge unused artifacts, prune completed pods, and recover disk space.

## 6. Comprehensive Documentation
- **Detailed Coordinate Transform Diagrams**: Traced exactly how pixel coordinates translate from EXIF to UTM `geo_data.txt`, through COLMAP sparse alignment (Sim3 transforms), raster grids, GeoTIFF, and finally to reverse-projected GeoJson polygons.
- **Rasterization Fallback Documentation**: Added explanations of the CUDA-based (`nvdiffrast`) vs. CPU-based barycentric orthomosaic projection fallback mechanisms.
- **State Machines documented**: Introduced full state machine flowcharts clarifying the lifecycle of pipeline steps and cancellation behavior through the Kafka `pipeline-control` topic.
