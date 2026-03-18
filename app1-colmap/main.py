import os
import json
import subprocess
import shutil
import threading
import time
import fcntl
import numpy as np
import rasterio
from rasterio.transform import from_origin
from exif import Image as ExifImage
from confluent_kafka import Consumer, Producer

# --- CONFIGURATION KAFKA ---
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.kafka.svc.cluster.local:9092")
TOPIC_IN = "vols-bruts"
TOPIC_OUT = "images-ortho"
TOPIC_STATUS = "pipeline-status"
TOPIC_CONTROL = "pipeline-control"

# --- GLOBAL CANCEL STATE ---
cancel_lock = threading.Lock()
current_mission_id = None
cancel_requested = False

class PipelineCancelledError(Exception):
    pass

# --- PIPELINE PARAMETER PROFILES ---
# Legacy profile: COLMAP 3.x parameters (capped for 64GB RAM environments)
LEGACY_PARAMS = {
    "feature_type": "SIFT",
    "feature_max_image_size": "3200",
    "feature_max_num_features": "32768",
    "matcher_type": "SIFT_BRUTEFORCE",
    "mapper_cmd": "mapper",
    "use_view_graph_calibrator": False,
    "read_orientation": False,
    "mvs_max_image_size": "2000",
    "mvs_window_step": "1",
    "mvs_filter_min_num_consistent": "1",
    "mvs_geom_consistency": "1",
    "fusion_max_image_size": "2000",
    "fusion_min_num_pixels": "1",
    "fusion_cache_size": "16",
    "use_mesh_ortho": False,
}

# Modern profile: COLMAP 4 ALIKED+GLOMAP+optimized MVS
MODERN_PARAMS = {
    "feature_type": "ALIKED_N16ROT",
    "feature_max_image_size": "3200",
    "feature_max_num_features": "8192",
    "matcher_type": "ALIKED_LIGHTGLUE",
    "mapper_cmd": "global_mapper",
    "use_view_graph_calibrator": True,
    "read_orientation": True,
    "mvs_max_image_size": "4000",
    "mvs_window_step": "2",
    "mvs_filter_min_num_consistent": "2",
    "mvs_geom_consistency": "1",
    "fusion_max_image_size": "4000",
    "fusion_min_num_pixels": "3",
    "fusion_cache_size": "32",
    "use_mesh_ortho": True,
}


def control_consumer_thread():
    control_consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'colmap-control-workers',
        'auto.offset.reset': 'latest'
    })
    control_consumer.subscribe([TOPIC_CONTROL])
    global cancel_requested
    
    while True:
        msg = control_consumer.poll(1.0)
        if msg is None: continue
        if msg.error(): continue
        try:
            data = json.loads(msg.value().decode('utf-8'))
            if data.get("command") == "cancel":
                with cancel_lock:
                    if current_mission_id == data.get("vol_id"):
                        cancel_requested = True
                        print(f"⚠️ Cancel requested for {current_mission_id}")
        except Exception:
            pass

threading.Thread(target=control_consumer_thread, daemon=True).start()

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'colmap-workers-v4',
    'auto.offset.reset': 'latest',
    'max.poll.interval.ms': 86400000  # 24 hours to prevent timeouts during long COLMAP jobs
})
consumer.subscribe([TOPIC_IN])
producer = Producer({'bootstrap.servers': KAFKA_BROKER})

def report_progress(vol_id, step, progress, status="processing", log=None):
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "COLMAP"}
    if log:
        msg["log"] = log
        print(f"[{step}] {log}")
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()

def run_command(command, vol_id, step, base_progress):
    """Execute a command, stream logs, and support async cancellation."""
    report_progress(vol_id, step, base_progress, log=f"Executing: {' '.join(command)}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    
    # Make stdout non-blocking to check cancellation even if COLMAP is silent
    flags = fcntl.fcntl(process.stdout, fcntl.F_GETFL)
    fcntl.fcntl(process.stdout, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    while True:
        with cancel_lock:
            if cancel_requested:
                process.kill()
                raise PipelineCancelledError("Mission cancelled by user")

        try:
            line = process.stdout.readline()
            if line:
                clean_line = line.strip()
                if clean_line:
                    report_progress(vol_id, step, base_progress, log=clean_line)
            else:
                if process.poll() is not None:
                    break
                time.sleep(0.1)
        except IOError:
            if process.poll() is not None:
                break
            time.sleep(0.1)
            
    return_code = process.wait()
    if return_code != 0:
        with cancel_lock:
            if cancel_requested:
                raise PipelineCancelledError("Mission cancelled by user")
        raise subprocess.CalledProcessError(return_code, command)

def extract_gps_data(image_dir, output_file, vol_id):
    import pyproj
    report_progress(vol_id, "GPS_EXTRACTION", 10, log="Extracting EXIF GPS data and converting to UTM...")
    images = [f for f in os.listdir(image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    count = 0
    utm_crs = None
    transformer = None
    with open(output_file, 'w') as f:
        for img_name in images:
            with cancel_lock:
                if cancel_requested:
                    raise PipelineCancelledError("Mission cancelled by user")
            img_path = os.path.join(image_dir, img_name)
            with open(img_path, 'rb') as src:
                try:
                    exif_img = ExifImage(src)
                    if hasattr(exif_img, 'gps_latitude') and hasattr(exif_img, 'gps_longitude'):
                        lat = exif_img.gps_latitude[0] + exif_img.gps_latitude[1]/60 + exif_img.gps_latitude[2]/3600
                        if getattr(exif_img, 'gps_latitude_ref', 'N') == 'S':
                            lat = -lat
                        lon = exif_img.gps_longitude[0] + exif_img.gps_longitude[1]/60 + exif_img.gps_longitude[2]/3600
                        if getattr(exif_img, 'gps_longitude_ref', 'E') == 'W':
                            lon = -lon
                        alt = getattr(exif_img, 'gps_altitude', 0)
                        
                        if transformer is None:
                            # Calculate UTM zone dynamically
                            zone_number = int((lon + 180) / 6) + 1
                            is_south = lat < 0
                            utm_crs = f"EPSG:32{'7' if is_south else '6'}{zone_number:02d}"
                            transformer = pyproj.Transformer.from_crs("EPSG:4326", utm_crs, always_xy=True)
                        
                        x, y = transformer.transform(lon, lat)
                        f.write(f"{img_name} {x} {y} {alt}\n")
                        count += 1
                except Exception: 
                    continue
    report_progress(vol_id, "GPS_EXTRACTION", 12, log=f"Extracted GPS from {count}/{len(images)} images. Using CRS {utm_crs}")
    return utm_crs


def detect_existing_pipeline(db_path):
    """Check if an existing database was created with SIFT or ALIKED features.
    Returns 'SIFT', 'ALIKED', or None if no database exists."""
    if not os.path.exists(db_path):
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # COLMAP 4 stores descriptor type in the descriptors table metadata
        # For COLMAP 3 databases, there's no type column — these are always SIFT
        cursor.execute("SELECT COUNT(*) FROM descriptors")
        count = cursor.fetchone()[0]
        conn.close()
        if count > 0:
            # If database exists with descriptors, it was created by a previous run.
            # COLMAP 3 databases are always SIFT. COLMAP 4 databases have typed descriptors
            # but for safety we always assume legacy databases are SIFT.
            return "SIFT"
    except Exception:
        pass
    return None


def generate_ortho_from_ply(ply_path, ortho_file, utm_crs, vol_id):
    """Generate an orthomosaic by projecting PLY point cloud to a 2D grid (legacy method)."""
    report_progress(vol_id, "ORTHO", 95, log="Generating orthomosaic from point cloud (PLY projection)...")
    from plyfile import PlyData
    plydata = PlyData.read(ply_path)
    x = plydata['vertex']['x']
    y = plydata['vertex']['y']
    z = plydata['vertex']['z']
    r = plydata['vertex']['red']
    g = plydata['vertex']['green']
    b = plydata['vertex']['blue']
    
    if len(x) == 0:
        raise ValueError("Point cloud is empty (0 vertices).")
    
    min_x, max_x = np.min(x), np.max(x)
    min_y, max_y = np.min(y), np.max(y)
    
    width_m = max_x - min_x
    height_m = max_y - min_y
    
    resolution = 0.05  # 5 cm/pixel
    
    # Cap the maximum dimension to 15000 pixels to avoid OOM
    max_dim = max(width_m, height_m)
    if max_dim > 0 and (max_dim / resolution) > 15000:
        resolution = max_dim / 15000.0
    elif max_dim == 0:
        resolution = 0.05
    
    width = max(1, int(np.ceil(width_m / resolution)))
    height = max(1, int(np.ceil(height_m / resolution)))
    
    img = np.zeros((height, width, 3), dtype=np.uint8)
    
    px = ((x - min_x) / resolution).astype(np.int32)
    py = ((max_y - y) / resolution).astype(np.int32)
    
    px = np.clip(px, 0, width - 1)
    py = np.clip(py, 0, height - 1)
    
    # Z-sorting to ensure highest points are drawn last
    order = np.argsort(z)
    px = px[order]
    py = py[order]
    img[py, px, 0] = r[order]
    img[py, px, 1] = g[order]
    img[py, px, 2] = b[order]
    
    img_data = img.transpose(2, 0, 1)  # (3, H, W)
    transform = from_origin(min_x, max_y, resolution, resolution)
    crs_to_use = utm_crs if utm_crs else 'EPSG:4326'
    
    with rasterio.open(
        ortho_file, 'w', driver='GTiff',
        height=height, width=width, count=3,
        dtype=img_data.dtype, crs=crs_to_use, transform=transform,
    ) as dst:
        dst.write(img_data)
    
    report_progress(vol_id, "ORTHO", 98, log=f"PLY ortho written: {width}x{height}px @ {resolution:.3f}m/px")


def generate_ortho_from_mesh(dense_path, ortho_file, utm_crs, vol_id):
    """Generate an orthomosaic via Poisson meshing + mesh texturing + rasterization."""
    fused_ply = os.path.join(dense_path, "fused.ply")
    meshed_ply = os.path.join(dense_path, "meshed-poisson.ply")
    
    # Step 1: Poisson meshing (fills holes)
    if not os.path.exists(meshed_ply):
        report_progress(vol_id, "MESHING", 93, log="Running Poisson surface reconstruction...")
        run_command([
            "colmap", "poisson_mesher",
            "--input_path", fused_ply,
            "--output_path", meshed_ply,
        ], vol_id, "MESHING", 93)
    else:
        report_progress(vol_id, "MESHING", 93, log="Poisson mesh found, skipping meshing.")
    
    # Step 2: Mesh texturing
    textured_dir = os.path.join(dense_path, "textured")
    textured_obj = os.path.join(textured_dir, "mesh.obj")
    
    if not os.path.exists(textured_obj):
        os.makedirs(textured_dir, exist_ok=True)
        report_progress(vol_id, "TEXTURING", 95, log="Running mesh texturing...")
        run_command([
            "colmap", "mesh_texturer",
            "--input_path", dense_path,
            "--input_mesh_path", meshed_ply,
            "--output_path", textured_dir,
        ], vol_id, "TEXTURING", 95)
    else:
        report_progress(vol_id, "TEXTURING", 95, log="Textured mesh found, skipping texturing.")
    
    # Step 3: Rasterize the textured mesh to GeoTIFF
    report_progress(vol_id, "ORTHO", 97, log="Rasterizing textured mesh to GeoTIFF...")
    try:
        import trimesh
        
        mesh = trimesh.load(textured_obj, process=False)
        vertices = np.array(mesh.vertices)
        
        if len(vertices) == 0:
            raise ValueError("Textured mesh has no vertices.")
        
        min_x, max_x = vertices[:, 0].min(), vertices[:, 0].max()
        min_y, max_y = vertices[:, 1].min(), vertices[:, 1].max()
        
        width_m = max_x - min_x
        height_m = max_y - min_y
        
        resolution = 0.05  # 5 cm/pixel
        max_dim = max(width_m, height_m)
        if max_dim > 0 and (max_dim / resolution) > 15000:
            resolution = max_dim / 15000.0
        elif max_dim == 0:
            resolution = 0.05
        
        raster_w = max(1, int(np.ceil(width_m / resolution)))
        raster_h = max(1, int(np.ceil(height_m / resolution)))
        
        # Create an orthographic scene looking down (-Z)
        scene = trimesh.Scene(mesh)
        
        # Render from directly above using orthographic projection
        # Build a grid of ray origins on the XY plane above the mesh
        max_z = vertices[:, 2].max() + 1.0
        
        xs = np.linspace(min_x, max_x, raster_w)
        ys = np.linspace(max_y, min_y, raster_h)  # flip Y for image coordinates
        
        img = np.zeros((raster_h, raster_w, 3), dtype=np.uint8)
        
        # Use trimesh ray casting in batches to avoid OOM
        ray_dir = np.array([0, 0, -1], dtype=np.float64)
        batch_size = min(raster_w, 512)
        
        for row_idx in range(raster_h):
            with cancel_lock:
                if cancel_requested:
                    raise PipelineCancelledError("Mission cancelled by user")
            
            origins = np.column_stack([
                xs,
                np.full(raster_w, ys[row_idx]),
                np.full(raster_w, max_z)
            ])
            directions = np.tile(ray_dir, (raster_w, 1))
            
            try:
                locations, index_ray, index_tri = mesh.ray.intersects_location(
                    ray_origins=origins,
                    ray_directions=directions,
                    multiple_hits=False
                )
                
                if len(index_ray) > 0:
                    # Get face colors from the textured mesh
                    if hasattr(mesh.visual, 'face_colors'):
                        colors = mesh.visual.face_colors[index_tri][:, :3]
                    elif hasattr(mesh.visual, 'vertex_colors'):
                        face_verts = mesh.faces[index_tri]
                        colors = mesh.visual.vertex_colors[face_verts[:, 0]][:, :3]
                    else:
                        colors = np.full((len(index_ray), 3), 128, dtype=np.uint8)
                    
                    img[row_idx, index_ray, :] = colors
            except Exception:
                continue
            
            if (row_idx + 1) % 500 == 0:
                report_progress(vol_id, "ORTHO", 97, log=f"Rasterizing mesh: row {row_idx+1}/{raster_h}")
        
        img_data = img.transpose(2, 0, 1)
        transform = from_origin(min_x, max_y, resolution, resolution)
        crs_to_use = utm_crs if utm_crs else 'EPSG:4326'
        
        with rasterio.open(
            ortho_file, 'w', driver='GTiff',
            height=raster_h, width=raster_w, count=3,
            dtype=img_data.dtype, crs=crs_to_use, transform=transform,
        ) as dst:
            dst.write(img_data)
        
        report_progress(vol_id, "ORTHO", 98, log=f"Mesh ortho written: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")
        
    except Exception as e:
        report_progress(vol_id, "ORTHO", 97, log=f"Mesh rasterization failed ({e}), falling back to PLY projection...")
        generate_ortho_from_ply(os.path.join(dense_path, "fused.ply"), ortho_file, utm_crs, vol_id)


def generate_dummy_ortho(ortho_file, fallback_x=0, fallback_y=0):
    """Generate a dummy black orthomosaic when no point cloud is available."""
    dummy_data = np.zeros((3, 1024, 1024), dtype=rasterio.uint8)
    transform = from_origin(fallback_x, fallback_y, 1, 1)
    with rasterio.open(
        ortho_file, 'w', driver='GTiff',
        height=1024, width=1024, count=3,
        dtype=dummy_data.dtype, crs='EPSG:4326', transform=transform,
    ) as dst:
        dst.write(dummy_data)


def run_colmap_pipeline(workspace_dir, raw_image_dir, vol_id, mission_params):
    try:
        # --- Pipeline selection ---
        pipeline_mode = mission_params.get("pipeline", "modern")
        if pipeline_mode not in ("modern", "legacy"):
            report_progress(vol_id, "WARNING", 1, log=f"Unknown pipeline '{pipeline_mode}', defaulting to 'modern'")
            pipeline_mode = "modern"
        
        params = MODERN_PARAMS if pipeline_mode == "modern" else LEGACY_PARAMS
        report_progress(vol_id, "PIPELINE", 1, log=f"Using {'🚀 COLMAP 4 Modern' if pipeline_mode == 'modern' else '🔧 Legacy (COLMAP 3 compat)'} pipeline")
        
        # --- 1. Preparation ---
        report_progress(vol_id, "PREPARING", 2, log=f"Creating workspace at {workspace_dir}")
        os.makedirs(workspace_dir, exist_ok=True)
        
        clean_images_dir = os.path.join(workspace_dir, "clean_images")
        os.makedirs(clean_images_dir, exist_ok=True)
        
        images = [f for f in os.listdir(raw_image_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        report_progress(vol_id, "COPYING_IMAGES", 5, log=f"Checking/Copying {len(images)} images to SSD...")
        
        copied_count = 0
        skipped_count = 0
        for i, img in enumerate(images):
            with cancel_lock:
                if cancel_requested:
                    raise PipelineCancelledError("Mission cancelled by user")
            
            src_path = os.path.join(raw_image_dir, img)
            dst_path = os.path.join(clean_images_dir, img)
            
            if os.path.exists(dst_path) and os.path.getsize(dst_path) == os.path.getsize(src_path):
                skipped_count += 1
            else:
                shutil.copy2(src_path, dst_path)
                copied_count += 1
                
            if (i + 1) % 50 == 0 or i == len(images) - 1:
                report_progress(vol_id, "COPYING_IMAGES", 5, log=f"Processed {i + 1}/{len(images)} images (Copied: {copied_count}, Skipped: {skipped_count})")
        
        db_path = os.path.join(workspace_dir, "database.db")
        sparse_path = os.path.join(workspace_dir, "sparse")
        geo_data_file = os.path.join(workspace_dir, "geo_data.txt")
        
        # --- Smart resume: check database descriptor type compatibility ---
        existing_type = detect_existing_pipeline(db_path)
        is_modern = pipeline_mode == "modern"
        
        if existing_type is not None:
            # Database exists from a previous run
            if is_modern and existing_type == "SIFT":
                report_progress(vol_id, "PREPARING", 3, log="⚠️ Existing database has SIFT features but modern (ALIKED) pipeline requested. Purging database for clean re-extraction.")
                os.remove(db_path)
            elif not is_modern and existing_type == "ALIKED":
                report_progress(vol_id, "PREPARING", 3, log="⚠️ Existing database has ALIKED features but legacy (SIFT) pipeline requested. Purging database for clean re-extraction.")
                os.remove(db_path)
            else:
                report_progress(vol_id, "PREPARING", 3, log=f"Existing database compatible ({existing_type}). Resuming...")
        
        # --- 2. GPS ---
        utm_crs = extract_gps_data(clean_images_dir, geo_data_file, vol_id)

        # --- 3. SfM: Feature Extraction ---
        sparse_done = os.path.exists(os.path.join(sparse_path, "0", "cameras.bin")) or os.path.exists(os.path.join(sparse_path, "0", "cameras.txt"))
        
        if not sparse_done:
            # Build feature extraction command based on pipeline
            feat_cmd = [
                "colmap", "feature_extractor",
                "--database_path", db_path,
                "--image_path", clean_images_dir,
                "--ImageReader.single_camera", "1",
                "--ImageReader.camera_model", "OPENCV",
            ]
            
            if is_modern:
                feat_cmd += [
                    "--FeatureExtraction.type", params["feature_type"],
                    "--AlikedExtraction.max_num_features", params["feature_max_num_features"],
                ]
            else:
                feat_cmd += [
                    "--FeatureExtraction.use_gpu", "1",
                    "--SiftExtraction.max_image_size", params["feature_max_image_size"],
                    "--SiftExtraction.max_num_features", params["feature_max_num_features"],
                ]
            
            run_command(feat_cmd, vol_id, "FEATURES", 15)
            
            # --- 4. SfM: Feature Matching ---
            match_cmd = [
                "colmap", "spatial_matcher",
                "--database_path", db_path,
                "--SpatialMatching.ignore_z", "1",
            ]
            
            if is_modern:
                match_cmd += [
                    "--FeatureMatching.type", params["matcher_type"],
                ]
            else:
                match_cmd += [
                    "--FeatureMatching.use_gpu", "1",
                ]
            
            run_command(match_cmd, vol_id, "MATCHING", 30)
            
            # --- 5. SfM: View Graph Calibration (modern only) ---
            if params["use_view_graph_calibrator"]:
                report_progress(vol_id, "CALIBRATING", 38, log="Running view graph calibration for GLOMAP...")
                run_command([
                    "colmap", "view_graph_calibrator",
                    "--database_path", db_path,
                ], vol_id, "CALIBRATING", 38)
            
            # --- 6. SfM: Mapping ---
            os.makedirs(sparse_path, exist_ok=True)
            map_cmd = [
                "colmap", params["mapper_cmd"],
                "--database_path", db_path,
                "--image_path", clean_images_dir,
                "--output_path", sparse_path,
            ]
            
            run_command(map_cmd, vol_id, "MAPPING", 45)
        else:
            report_progress(vol_id, "MAPPING", 45, log="Sparse model found. Skipping SfM extraction and matching.")

        # --- 7. Alignment ---
        sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
        os.makedirs(sparse_geo_path, exist_ok=True)
        align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin")) or os.path.exists(os.path.join(sparse_geo_path, "cameras.txt"))
        
        if not align_done:
            if os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0:
                run_command([
                    "colmap", "model_aligner",
                    "--input_path", os.path.join(sparse_path, "0"),
                    "--output_path", sparse_geo_path,
                    "--ref_images_path", geo_data_file,
                    "--ref_is_gps", "0",
                    "--alignment_max_error", "0.2"
                ], vol_id, "ALIGNING", 60)
            else:
                report_progress(vol_id, "ALIGNING", 60, log="No GPS data found, skipping aligner.")
                shutil.copytree(os.path.join(sparse_path, "0"), sparse_geo_path, dirs_exist_ok=True)
        else:
            report_progress(vol_id, "ALIGNING", 60, log="Aligned model found. Skipping alignment.")

        # --- 8. MVS ---
        dense_path = os.path.join(workspace_dir, "dense")
        
        # Undistorter
        if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
            run_command([
                "colmap", "image_undistorter",
                "--image_path", clean_images_dir,
                "--input_path", sparse_geo_path,
                "--output_path", dense_path,
            ], vol_id, "UNDISTORT", 70)
        else:
            report_progress(vol_id, "UNDISTORT", 70, log="Undistorted images and fusion.cfg found. Skipping undistortion.")
            
        # PatchMatchStereo (natively resumes — skips existing depth maps)
        run_command([
            "colmap", "patch_match_stereo",
            "--workspace_path", dense_path,
            "--PatchMatchStereo.gpu_index", "0",
            "--PatchMatchStereo.max_image_size", params["mvs_max_image_size"],
            "--PatchMatchStereo.window_step", params["mvs_window_step"],
            "--PatchMatchStereo.filter", "1",
            "--PatchMatchStereo.filter_min_num_consistent", params["mvs_filter_min_num_consistent"],
            "--PatchMatchStereo.geom_consistency", params["mvs_geom_consistency"],
        ], vol_id, "STEREO", 80)
            
        # Stereo Fusion
        if not os.path.exists(os.path.join(dense_path, "fused.ply")):
            run_command([
                "colmap", "stereo_fusion",
                "--workspace_path", dense_path,
                "--output_path", os.path.join(dense_path, "fused.ply"),
                "--StereoFusion.max_image_size", params["fusion_max_image_size"],
                "--StereoFusion.cache_size", params["fusion_cache_size"],
                "--StereoFusion.min_num_pixels", params["fusion_min_num_pixels"],
            ], vol_id, "FUSION", 90)
        else:
            report_progress(vol_id, "FUSION", 90, log="Fused point cloud found. Skipping stereo fusion.")
        
        # --- 9. Orthomosaic Generation ---
        ortho_file = os.path.join(workspace_dir, "orthomosaic.tif")
        ply_path = os.path.join(dense_path, "fused.ply")
        
        if os.path.exists(ply_path):
            try:
                if params["use_mesh_ortho"]:
                    generate_ortho_from_mesh(dense_path, ortho_file, utm_crs, vol_id)
                else:
                    generate_ortho_from_ply(ply_path, ortho_file, utm_crs, vol_id)
            except Exception as e:
                report_progress(vol_id, "ORTHO", 95, log=f"Error generating ortho: {e}. Using dummy.")
                generate_dummy_ortho(ortho_file)
        else:
            report_progress(vol_id, "ORTHO", 95, log="fused.ply not found. Creating dummy ortho (origin 0,0).")
            generate_dummy_ortho(ortho_file)
        
        report_progress(vol_id, "DONE", 100, status="success", log="Pipeline complete!")
        
        # Send to Tiler
        msg = {
            "vol_id": vol_id,
            "ortho_path": ortho_file,
            "classes": mission_params.get("classes", ["car"]),
            "ai_confidence": mission_params.get("ai_confidence", 0.3)
        }
        producer.produce(TOPIC_OUT, key=vol_id, value=json.dumps(msg))
        producer.flush()

    except PipelineCancelledError as e:
        report_progress(vol_id, "CANCELLED", 0, status="error", log=f"🚫 {str(e)}")
    except Exception as e:
        report_progress(vol_id, "ERROR", 0, status="error", log=f"CRITICAL ERROR: {str(e)}")

print("🎧 App 1 (COLMAP 4 — ALIKED/GLOMAP + Legacy Fallback) ready.")

try:
    while True:
        msg = consumer.poll(1.0)
        if msg is None: continue
        if msg.error(): continue
            
        try:
            val = msg.value().decode('utf-8')
            mission = json.loads(val)
            vol_id = mission['vol_id']
            
            with cancel_lock:
                current_mission_id = vol_id
                cancel_requested = False
            
            def make_host_path(path):
                if path.startswith("/host"): return path
                if not path.startswith("/"): path = "/" + path
                return "/host" + path
                
            ws_base = mission.get('workspace_dir', '/home/olivier/workspace')
            work_dir = make_host_path(os.path.join(ws_base, vol_id))
            input_dir = make_host_path(mission["input_dir"])
            
            print(f"📦 Processing mission {vol_id}")
            print(f"   Input: {input_dir}")
            print(f"   Workspace: {work_dir}")
            print(f"   Pipeline: {mission.get('pipeline', 'modern')}")
            
            if not os.path.exists(input_dir):
                error_msg = f"Input directory not found: {input_dir}"
                print(f"❌ {error_msg}")
                report_progress(vol_id, "ERROR", 0, status="error", log=error_msg)
                continue
                
            run_colmap_pipeline(work_dir, input_dir, vol_id, mission)
            
            with cancel_lock:
                current_mission_id = None
                cancel_requested = False
                
        except Exception as e:
            print(f"Loop error: {e}")
except KeyboardInterrupt:
    pass
finally:
    consumer.close()
