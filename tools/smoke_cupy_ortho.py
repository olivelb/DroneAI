#!/usr/bin/env python3
"""
Manually test the CuPy ortho rendering pipeline using an existing final.ply.
Skips training entirely — just loads the PLY, applies geo-alignment,
filters, renders, and writes GeoTIFF.

Usage (inside the pod):
  python3 /app/tools/smoke_cupy_ortho.py /work/local/my-mission/dense \
      /work/local/my-mission/gaussian_checkpoints/final.ply \
      /work/local/my-mission/test_cupy_ortho.tif

This file is intentionally outside the automated pytest suite because it
requires a CUDA GPU and mission-specific reconstruction artifacts.
"""

import argparse
import gc
import math
import os
import sys
import time
from pathlib import Path

import cupy as cp
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("dense_path", help="COLMAP dense reconstruction directory")
parser.add_argument("checkpoint_ply", help="Trained Gaussian PLY checkpoint")
parser.add_argument("output_ortho", help="Output GeoTIFF path")
parser.add_argument("--crs", default="EPSG:32631", help="Projected output CRS")
parser.add_argument("--resolution", type=float, default=0.02, help="Metres per pixel")
parser.add_argument("--sh-degree", type=int, default=3)
parser.add_argument("--transform", help="Optional alignment_transform.json")
parser.add_argument(
    "--source-root",
    default=os.getenv("DRONEAI_SOURCE_ROOT", str(Path(__file__).resolve().parents[1])),
    help="DroneAI source root containing app1-colmap",
)
args = parser.parse_args()

DENSE_PATH = os.path.realpath(args.dense_path)
CHECKPOINT_PLY = os.path.realpath(args.checkpoint_ply)
OUTPUT_ORTHO = os.path.realpath(args.output_ortho)
UTM_CRS = args.crs
RESOLUTION = args.resolution
SH_DEGREE = args.sh_degree

# Look for alignment_transform.json
TRANSFORM_FILE = os.path.realpath(args.transform) if args.transform else None
for p in [TRANSFORM_FILE, os.path.join(DENSE_PATH, "..", "alignment_transform.json")]:
    if not p:
        continue
    if os.path.isfile(p):
        TRANSFORM_FILE = os.path.realpath(p)
        break


def log(step, pct, msg=""):
    print(f"[{step} {pct:3d}%] {msg}")


print("=" * 60)
print("CuPy Gaussian Ortho Pipeline — render-only test")
print("=" * 60)
print(f"Dense path  : {DENSE_PATH}")
print(f"PLY file    : {CHECKPOINT_PLY}")
print(f"Output      : {OUTPUT_ORTHO}")
print(f"CRS         : {UTM_CRS}")
print(f"Resolution  : {RESOLUTION} m/px")
print(f"Transform   : {TRANSFORM_FILE or 'None (PCA path)'}")

# Check GPU
mem_free, mem_total = cp.cuda.Device(0).mem_info
gpu_props = cp.cuda.runtime.getDeviceProperties(0)
print(f"GPU         : {gpu_props['name'].decode()} ({mem_free // 2**20} / {mem_total // 2**20} MB free)")
print()

t0 = time.time()

# --- 1. Load COLMAP reconstruction ---
log("LOAD", 5, "Loading COLMAP reconstruction...")
# Load the selected source tree rather than a developer-specific host path.
sys.path.insert(0, os.path.join(os.path.realpath(args.source_root), "app1-colmap"))
from gaussian_ortho.colmap_loader import apply_sim3_to_points, load_colmap_reconstruction
from gaussian_ortho.exif_altitude import compute_colmap_scale, extract_exif_altitudes
from gaussian_ortho.gaussian_model import GaussianModel
from gaussian_ortho.geo_writer import write_geotiff
from gaussian_ortho.ortho_renderer import compute_ortho_extent, render_orthophoto

images_dir = os.path.join(DENSE_PATH, "images")

train_cameras, test_cameras, point_cloud, transform_data = load_colmap_reconstruction(
    DENSE_PATH,
    TRANSFORM_FILE,
)
log("LOAD", 10, f"Loaded {len(train_cameras)} cameras, {point_cloud.points.shape[0]} points")

# --- 2. Extract EXIF altitudes & compute scale ---
exif_altitudes = extract_exif_altitudes(images_dir)
cam_alts = [exif_altitudes.get(cam.image_name, None) for cam in train_cameras]
valid_alts = [a for a in cam_alts if a is not None]
mean_exif_alt = np.mean(valid_alts) if valid_alts else None

if transform_data:
    colmap_to_meters = float(transform_data.get("scale", 1.0))
    log("LOAD", 12, f"Sim3 transform loaded (scale={colmap_to_meters:.6f})")
else:
    colmap_to_meters = compute_colmap_scale(train_cameras, images_dir, UTM_CRS)
    log("LOAD", 12, f"COLMAP->metres scale: {colmap_to_meters:.4f}")

# --- 3. Load existing PLY ---
log("MODEL", 15, f"Loading PLY: {CHECKPOINT_PLY}")
merged_model = GaussianModel(sh_degree=SH_DEGREE, fagk_enabled=True)
merged_model.load_ply(CHECKPOINT_PLY)
merged_model.active_sh_degree = SH_DEGREE
log("MODEL", 20, f"Loaded {merged_model.num_gaussians} Gaussians")

# --- 4. Geo-alignment ---
R_geo = None
geo_origin = np.zeros(3, dtype=np.float64)

if transform_data:
    log("GEO", 30, "Applying Sim3 geo-alignment...")
    R = cp.array(transform_data["R"], dtype=cp.float32)
    s = float(transform_data["scale"])
    t_f64 = np.array(transform_data["t"], dtype=np.float64)

    merged_model._xyz = (s * (R @ merged_model._xyz.T)).T
    merged_model._scaling += math.log(s)
    R_quat = merged_model._matrix_to_quaternion(cp.asnumpy(R))
    R_quat_cp = cp.array(R_quat, dtype=cp.float32)
    merged_model._rotation = merged_model._quaternion_multiply(
        R_quat_cp[None, :],
        merged_model._rotation,
    )
    geo_origin = t_f64

    geo_cam_positions = apply_sim3_to_points(np.array([c.T for c in train_cameras], dtype=np.float64), transform_data)
    log("GEO", 35, f"Applied Sim3: scale={s:.6f}")
else:
    log("GEO", 30, "Computing PCA nadir direction...")
    from gaussian_ortho.exif_altitude import extract_exif_gps
    from gaussian_ortho.pca_alignment import compute_pca_rotation
    from pyproj import Transformer as _Transformer

    cam_positions = np.array([c.T for c in train_cameras], dtype=np.float64)
    R_align, angle_deg = compute_pca_rotation(train_cameras, point_cloud.points)
    R_geo = R_align.astype(np.float32)
    log("GEO", 33, f"PCA nadir: {angle_deg:.1f} deg from Z")

    geo_cam_positions = (R_align @ cam_positions.T).T

    _gps = extract_exif_gps(images_dir)
    _t_proj = _Transformer.from_crs("EPSG:4326", UTM_CRS, always_xy=True)
    _utm_pts = []
    for cam in train_cameras:
        g = _gps.get(cam.image_name)
        if g is not None:
            e, n = _t_proj.transform(g[1], g[0])
            _utm_pts.append([e, n, mean_exif_alt or 0.0])
    if _utm_pts:
        _gps_centroid = np.mean(_utm_pts, axis=0).astype(np.float64)
        _model_centroid = geo_cam_positions.mean(axis=0) * colmap_to_meters
        geo_origin = _gps_centroid - _model_centroid
        log("GEO", 35, f"GeoTIFF origin: E={geo_origin[0]:.2f}, N={geo_origin[1]:.2f}")

# --- 5. Filter ---
if transform_data:
    local_cam_positions = geo_cam_positions - geo_origin
else:
    local_cam_positions = np.array([c.T for c in train_cameras], dtype=np.float64)

del point_cloud

log("FILTER", 40, "Filtering Gaussians...")
from gaussian_ortho.model_filtering import filter_gaussians

filter_gaussians(
    merged_model,
    local_cam_positions,
    max_scale=1.0,
    dist_multiplier=1.0,
    opacity_threshold=0.005,
    needle_ratio=0.0,
    sor_sigma=4.0,
    sor_enabled=False,
    cc_enabled=False,
    z_floater_enabled=False,
    R_geo=R_geo,
    report_fn=lambda msg: log("FILTER", 40, msg),
)
log("FILTER", 50, f"After filtering: {merged_model.num_gaussians} Gaussians")

gc.collect()
cp.get_default_memory_pool().free_all_blocks()

# --- 6. Compute extent and render ---
model_extent = compute_ortho_extent(merged_model, pad=2.0, R_geo=R_geo)
log("RENDER", 60, f"Ortho extent: {model_extent}")

local_gsd = RESOLUTION if transform_data else RESOLUTION / colmap_to_meters

log("RENDER", 65, f"Rendering at {RESOLUTION} m/px (local GSD={local_gsd:.6f})...")
t_render = time.time()

result = render_orthophoto(
    merged_model,
    gsd=local_gsd,
    extent=model_extent,
    R_geo=R_geo,
)

dt_render = time.time() - t_render
rgb = result["rgb"]
height = result["height"]
x_min, x_max, y_min, y_max = result["extent"]
H, W = rgb.shape[:2]
log("RENDER", 90, f"Rendered {W}x{H} px in {dt_render:.1f}s")

# --- 7. Write GeoTIFF ---
if transform_data:
    geo_x_min = float(np.float64(x_min) + geo_origin[0])
    geo_y_max = float(np.float64(y_max) + geo_origin[1])
else:
    geo_x_min = float(np.float64(x_min) * colmap_to_meters + geo_origin[0])
    geo_y_max = float(np.float64(y_max) * colmap_to_meters + geo_origin[1])

if not transform_data and colmap_to_meters != 1.0:
    height = height * colmap_to_meters
if mean_exif_alt is not None:
    z_offset = mean_exif_alt - np.mean(height)
    height = height + z_offset

log("WRITE", 95, "Writing GeoTIFF...")
height_file = str(Path(OUTPUT_ORTHO).with_suffix(".height.tif"))
write_geotiff(
    output_path=OUTPUT_ORTHO,
    rgb=rgb,
    x_min=geo_x_min,
    y_max=geo_y_max,
    gsd=RESOLUTION,
    crs=UTM_CRS,
    height_map=height,
    height_output_path=height_file,
)

dt_total = time.time() - t0
log("DONE", 100, f"Total: {dt_total:.1f}s")
print(f"\nOutput: {OUTPUT_ORTHO}")
print(f"Height: {height_file}")
print(f"Size  : {W}x{H} ({os.path.getsize(OUTPUT_ORTHO) / 2**20:.1f} MB)")
