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
DEFAULT_WORKSPACE_ROOT = os.getenv("WORKSPACE_DIR", "/mnt/j/workspace")

# --- GLOBAL CANCEL STATE ---
cancel_lock = threading.Lock()
current_mission_id = None
cancel_requested = False

class PipelineCancelledError(Exception):
    pass

# --- PIPELINE PARAMETER PROFILES ---
# Legacy profile: SIFT defaults (COLMAP 4)
LEGACY_PARAMS = {
    "feature_type": "SIFT",
    "feature_max_image_size": "4000",
    "feature_max_num_features": "8192",
    "matcher_type": "SIFT",
    "mapper_cmd": "mapper",
    "use_view_graph_calibrator": False,
    "read_orientation": False,
    "mvs_max_image_size": "4000",
    "mvs_gpu_index": "-1",
    "mvs_num_iterations": "3",
    "mvs_num_samples": "10",
    "mvs_window_step": "1",
    "mvs_filter_min_num_consistent": "2",
    "fusion_max_image_size": "4000",
    "fusion_min_num_pixels": "5",
    "fusion_cache_size": "32",
    "ortho_mesh_resolution": "0.02",
    "ortho_mesh_max_dimension": "12000",
    "ortho_mesh_rasterizer": "cuda",
    "ortho_mesh_min_normal_cos": "0.5",
    "ortho_mesh_require_upward": True,
    "ortho_mesh_gap_fill_passes": "3",
    "use_mesh_ortho": True,
}

# Modern profile: COLMAP 4 ALIKED + LIGHTGLUE defaults
MODERN_PARAMS = {
    "feature_type": "ALIKED_N16ROT",
    "feature_max_image_size": "4000",
    "feature_max_num_features": "2048",
    "matcher_type": "ALIKED_LIGHTGLUE",
    "mapper_cmd": "global_mapper",
    "use_view_graph_calibrator": True,
    "read_orientation": True,
    "mvs_max_image_size": "4000",
    "mvs_gpu_index": "-1",
    "mvs_num_iterations": "3",
    "mvs_num_samples": "15",
    "mvs_window_step": "1",
    "mvs_filter_min_num_consistent": "2",
    "fusion_max_image_size": "4000",
    "fusion_min_num_pixels": "5",
    "fusion_cache_size": "32",
    "ortho_mesh_resolution": "0.02",
    "ortho_mesh_max_dimension": "12000",
    "ortho_mesh_rasterizer": "cuda",
    "ortho_mesh_min_normal_cos": "0.5",
    "ortho_mesh_require_upward": True,
    "ortho_mesh_gap_fill_passes": "3",
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


def create_consumer():
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'colmap-workers-v4',
        'auto.offset.reset': 'latest',
        'max.poll.interval.ms': 86400000  # 24 hours to prevent timeouts during long COLMAP jobs
    })
    consumer.subscribe([TOPIC_IN])
    return consumer


producer = Producer({'bootstrap.servers': KAFKA_BROKER})


def resolve_workspace_dir(workspace_value, vol_id):
    workspace_root = os.path.normpath(workspace_value or DEFAULT_WORKSPACE_ROOT)
    if os.path.basename(workspace_root) == vol_id:
        return workspace_root
    return os.path.join(workspace_root, vol_id)

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


def read_saved_utm_crs(geo_data_file):
    crs_file = f"{geo_data_file}.crs"
    if not os.path.exists(crs_file):
        return None
    try:
        with open(crs_file, 'r', encoding='utf-8') as handle:
            value = handle.read().strip()
        return value or None
    except OSError:
        return None


def save_utm_crs(geo_data_file, utm_crs):
    if not utm_crs:
        return
    crs_file = f"{geo_data_file}.crs"
    try:
        with open(crs_file, 'w', encoding='utf-8') as handle:
            handle.write(f"{utm_crs}\n")
    except OSError:
        pass


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
        # For COLMAP 3 databases, there's no type column — these are always SIFT.
        # However, we can distinguish by checking the bytes per feature.
        # SIFT: uint8 (1 byte) * 128 cols = 128 bytes per feature
        # ALIKED: float32 (4 bytes) * 128 cols = 512 bytes per feature
        cursor.execute("SELECT rows, cols, length(data) FROM descriptors LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if row is not None:
            rows, cols, blob_size = row
            if rows > 0:
                bytes_per_feature = blob_size / rows
                if bytes_per_feature == 128:
                    return "SIFT"
                elif bytes_per_feature == 512:
                    return "ALIKED"
    except Exception:
        pass
    return None


def apply_iterative_gap_fill(img, passes=3):
    from scipy.ndimage import uniform_filter, maximum_filter

    mask = np.any(img > 0, axis=2).astype(np.uint8)
    fill_before = np.count_nonzero(mask)
    for _ in range(passes):
        dilated_mask = maximum_filter(mask, size=3)
        new_pixels = (dilated_mask > 0) & (mask == 0)
        if not np.any(new_pixels):
            break
        mask_f = mask.astype(np.float32)
        neighbor_count = uniform_filter(mask_f, size=3, mode='constant') * 9
        for channel_index in range(3):
            channel = img[:, :, channel_index].astype(np.float32)
            neighbor_sum = uniform_filter(channel * mask_f, size=3, mode='constant') * 9
            fill_vals = np.zeros_like(channel, dtype=np.float32)
            np.divide(neighbor_sum, neighbor_count, out=fill_vals, where=neighbor_count > 0)
            fill_vals = fill_vals.astype(np.uint8)
            img[:, :, channel_index] = np.where(new_pixels, fill_vals, img[:, :, channel_index])
        mask = dilated_mask
    fill_after = np.count_nonzero(mask)
    return img, fill_before, fill_after


def generate_ortho_from_ply(ply_path, ortho_file, utm_crs, vol_id, transform_file=None):
    """Generate an orthomosaic by projecting PLY point cloud to a 2D grid.
    
    If transform_file is provided, the PLY is assumed to be in COLMAP coords
    and is geo-referenced in float64 internally to avoid float32 precision loss.
    """
    report_progress(vol_id, "ORTHO", 95, log="Generating orthomosaic from point cloud (PLY projection)...")
    from plyfile import PlyData
    plydata = PlyData.read(ply_path)
    x = np.array(plydata['vertex']['x'], dtype=np.float64)
    y = np.array(plydata['vertex']['y'], dtype=np.float64)
    z = np.array(plydata['vertex']['z'], dtype=np.float64)
    r = np.array(plydata['vertex']['red'])
    g = np.array(plydata['vertex']['green'])
    b = np.array(plydata['vertex']['blue'])
    
    if len(x) == 0:
        raise ValueError("Point cloud is empty (0 vertices).")
    
    # Apply geo-alignment in float64 to preserve sub-cm precision at UTM scale
    if transform_file and os.path.exists(transform_file):
        with open(transform_file, 'r') as tf:
            tdata = json.load(tf)
        R = np.array(tdata["R"])
        scale = tdata["scale"]
        t = np.array(tdata["t"])
        xyz = np.column_stack([x, y, z])
        xyz_geo = (scale * (R @ xyz.T) + t[:, np.newaxis]).T
        x, y, z = xyz_geo[:, 0], xyz_geo[:, 1], xyz_geo[:, 2]
        report_progress(vol_id, "ORTHO", 95, log=f"Applied geo-transform in float64 ({len(x)} pts)")
    
    min_x, max_x = float(np.min(x)), float(np.max(x))
    min_y, max_y = float(np.min(y)), float(np.max(y))
    
    width_m = max_x - min_x
    height_m = max_y - min_y
    
    resolution = 0.05  # 5 cm/pixel
    max_dim = max(width_m, height_m)
    if max_dim > 0 and (max_dim / resolution) > 15000:
        resolution = max_dim / 15000.0
    elif max_dim == 0:
        resolution = 0.05
    
    width = max(1, int(np.ceil(width_m / resolution)))
    height = max(1, int(np.ceil(height_m / resolution)))
    
    img = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Use local coords for pixel indices (subtract origin in float64 before casting)
    px = ((x - min_x) / resolution).astype(np.int32)
    py = ((max_y - y) / resolution).astype(np.int32)
    
    px = np.clip(px, 0, width - 1)
    py = np.clip(py, 0, height - 1)
    
    # Z-sorting to ensure highest points are drawn last
    order = np.argsort(z)
    img[py[order], px[order], 0] = r[order]
    img[py[order], px[order], 1] = g[order]
    img[py[order], px[order], 2] = b[order]
    
    # Fill gaps between scattered points using iterative neighbor averaging.
    # Point cloud projections have isolated pixels; YOLO needs contiguous imagery.
    img, fill_before, fill_after = apply_iterative_gap_fill(img)
    report_progress(vol_id, "ORTHO", 97, log=f"Gap-fill: {100*fill_before/(width*height):.1f}% -> {100*fill_after/(width*height):.1f}%")
    
    img_data = img.transpose(2, 0, 1)  # (3, H, W)
    # GeoTIFF origin uses float64 min_x/max_y for full UTM precision
    transform = from_origin(min_x, max_y, resolution, resolution)
    crs_to_use = utm_crs if utm_crs else 'EPSG:4326'
    
    with rasterio.open(
        ortho_file, 'w', driver='GTiff',
        height=height, width=width, count=3,
        dtype=img_data.dtype, crs=crs_to_use, transform=transform,
    ) as dst:
        dst.write(img_data)
    
    report_progress(vol_id, "ORTHO", 98, log=f"PLY ortho written: {width}x{height}px @ {resolution:.3f}m/px")


def generate_ortho_from_mesh(dense_path, ortho_file, utm_crs, vol_id, workspace_dir=None, params=None):
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
    textured_mesh = os.path.join(textured_dir, "mesh.ply")
    
    if not os.path.exists(textured_mesh):
        os.makedirs(textured_dir, exist_ok=True)
        report_progress(vol_id, "TEXTURING", 95, log="Running mesh texturing...")
        run_command([
            "colmap", "mesh_texturer",
            "--workspace_path", dense_path,
            "--input_path", meshed_ply,
            "--output_path", textured_dir,
        ], vol_id, "TEXTURING", 95)
    else:
        report_progress(vol_id, "TEXTURING", 95, log="Textured mesh found, skipping texturing.")
    
    # Step 3: Rasterize the textured mesh to GeoTIFF.
    # Preferred path: headless GPU rasterization of the textured mesh.
    # Fallback: dense CPU surface sampling if EGL/OpenGL is unavailable.
    report_progress(vol_id, "ORTHO", 97, log="Rasterizing textured mesh to GeoTIFF...")
    try:
        from PIL import Image as PILImage
        PILImage.MAX_IMAGE_PIXELS = None

        params = params or {}
        requested_resolution = float(os.getenv("ORTHO_MESH_RESOLUTION", params.get("ortho_mesh_resolution", "0.02")))
        max_dimension = int(os.getenv("ORTHO_MESH_MAX_DIMENSION", params.get("ortho_mesh_max_dimension", "12000")))
        rasterizer = os.getenv("ORTHO_MESH_RASTERIZER", params.get("ortho_mesh_rasterizer", "cuda")).lower()
        min_normal_cos = float(os.getenv("ORTHO_MESH_MIN_NORMAL_COS", params.get("ortho_mesh_min_normal_cos", "0.5")))
        require_upward = str(os.getenv("ORTHO_MESH_REQUIRE_UPWARD", str(params.get("ortho_mesh_require_upward", True)))).lower() in ("1", "true", "yes", "on")
        gap_fill_passes = int(os.getenv("ORTHO_MESH_GAP_FILL_PASSES", params.get("ortho_mesh_gap_fill_passes", "3")))

        with open(textured_mesh, 'rb') as f:
            header_lines = []
            while True:
                line = f.readline().decode('ascii').strip()
                header_lines.append(line)
                if line == 'end_header':
                    break
            header_offset = f.tell()

        n_verts = n_faces = 0
        for line in header_lines:
            if line.startswith('element vertex'):
                n_verts = int(line.split()[-1])
            elif line.startswith('element face'):
                n_faces = int(line.split()[-1])

        if n_verts == 0 or n_faces == 0:
            raise ValueError(f"Textured mesh has {n_verts} vertices, {n_faces} faces.")

        report_progress(vol_id, "ORTHO", 97, log=f"Loading mesh: {n_verts:,} vertices, {n_faces:,} faces...")
        with open(textured_mesh, 'rb') as f:
            f.seek(header_offset)
            vertices = np.frombuffer(f.read(n_verts * 12), dtype='<f4').reshape(-1, 3).astype(np.float64)

        transform_file = os.path.join(workspace_dir, "alignment_transform.json") if workspace_dir else None
        if transform_file and os.path.exists(transform_file):
            with open(transform_file, 'r') as tf:
                tdata = json.load(tf)
            R_a = np.array(tdata["R"])
            s_a = tdata["scale"]
            t_a = np.array(tdata["t"])
            vertices = (s_a * (R_a @ vertices.T) + t_a[:, np.newaxis]).T
            report_progress(vol_id, "ORTHO", 97, log="Applied geo-alignment to mesh vertices")

        min_x, max_x = vertices[:, 0].min(), vertices[:, 0].max()
        min_y, max_y = vertices[:, 1].min(), vertices[:, 1].max()
        min_z, max_z = vertices[:, 2].min(), vertices[:, 2].max()
        width_m = max_x - min_x
        height_m = max_y - min_y
        resolution = max(requested_resolution, width_m / max_dimension if width_m > 0 else requested_resolution, height_m / max_dimension if height_m > 0 else requested_resolution)
        raster_w = max(1, int(np.ceil(width_m / resolution)))
        raster_h = max(1, int(np.ceil(height_m / resolution)))
        report_progress(vol_id, "ORTHO", 97, log=f"Raster: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

        local_vertices = np.empty((n_verts, 3), dtype=np.float32)
        local_vertices[:, 0] = (vertices[:, 0] - min_x).astype(np.float32)
        local_vertices[:, 1] = (vertices[:, 1] - min_y).astype(np.float32)
        local_vertices[:, 2] = vertices[:, 2].astype(np.float32)
        del vertices

        face_dtype = np.dtype([
            ('n_vert', 'u1'), ('v0', '<i4'), ('v1', '<i4'), ('v2', '<i4'), ('n_tc', 'u1'),
            ('u0', '<f4'), ('t0', '<f4'), ('u1', '<f4'), ('t1', '<f4'), ('u2', '<f4'), ('t2', '<f4'),
        ])
        texture_path = os.path.join(textured_dir, "texture.png")

        def write_geotiff(image):
            img_data = image.transpose(2, 0, 1)
            transform = from_origin(min_x, max_y, resolution, resolution)
            crs_to_use = utm_crs if utm_crs else 'EPSG:4326'
            with rasterio.open(
                ortho_file, 'w', driver='GTiff',
                height=raster_h, width=raster_w, count=3,
                dtype=img_data.dtype, crs=crs_to_use, transform=transform,
            ) as dst:
                dst.write(img_data)

        if rasterizer == "cuda":
            try:
                import torch
                import nvdiffrast.torch as dr

                if not torch.cuda.is_available():
                    raise RuntimeError("torch.cuda.is_available() is false")

                device = torch.device("cuda")
                report_progress(vol_id, "ORTHO", 97, log="Initializing CUDA rasterizer...")
                glctx = dr.RasterizeCudaContext(device=device)
                report_progress(vol_id, "ORTHO", 97, log="Loading texture for CUDA rasterizer...")
                texture_image = np.array(PILImage.open(texture_path).convert('RGB'), dtype=np.float32) / np.float32(255.0)
                texture_filter_mode = 'linear'
                texture_sampling_kwargs = {'boundary_mode': 'clamp'}

                tex_h, tex_w = texture_image.shape[:2]
                can_build_full_mips = True
                mip_test_w = tex_w
                mip_test_h = tex_h
                while mip_test_w > 1 or mip_test_h > 1:
                    if (mip_test_w > 1 and (mip_test_w & 1)) or (mip_test_h > 1 and (mip_test_h & 1)):
                        can_build_full_mips = False
                        break
                    mip_test_w = max(1, mip_test_w // 2)
                    mip_test_h = max(1, mip_test_h // 2)

                if not can_build_full_mips:
                    target_long_side = int(max(raster_w, raster_h) * 2)
                    resize_scale = min(1.0, target_long_side / float(max(tex_w, tex_h)))
                    if resize_scale < 1.0:
                        resized_w = max(2, int(round(tex_w * resize_scale)))
                        resized_h = max(2, int(round(tex_h * resize_scale)))
                        if resized_w % 2:
                            resized_w += 1
                        if resized_h % 2:
                            resized_h += 1
                        report_progress(vol_id, "ORTHO", 97, log=f"Prefiltering texture atlas for CUDA rasterizer: {tex_w}x{tex_h} -> {resized_w}x{resized_h}")
                        texture_image = np.array(
                            PILImage.fromarray((texture_image * 255.0).astype(np.uint8)).resize((resized_w, resized_h), PILImage.Resampling.LANCZOS),
                            dtype=np.float32,
                        ) / np.float32(255.0)
                        tex_h, tex_w = texture_image.shape[:2]

                texture = torch.from_numpy(texture_image).to(device=device).unsqueeze(0).contiguous()
                max_tex_scale = max(texture.shape[2] / max(raster_h, 1), texture.shape[1] / max(raster_w, 1), 1.0)
                if can_build_full_mips:
                    mip_bias_value = float(np.log2(max_tex_scale)) if max_tex_scale > 1.0 else 0.0
                    texture_filter_mode = 'linear-mipmap-linear'
                    texture_sampling_kwargs['mip_level_bias'] = torch.full((1, raster_h, raster_w), mip_bias_value, dtype=torch.float32, device=device)
                    texture_sampling_kwargs['mip'] = dr.texture_construct_mip(texture, max_mip_level=6)

                report_progress(vol_id, "ORTHO", 97, log="Allocating CUDA ortho buffers...")
                final_depth = torch.full((raster_h, raster_w), 1.0, dtype=torch.float32, device=device)
                final_color = torch.zeros((raster_h, raster_w, 3), dtype=torch.float16, device=device)
                total_candidate_faces = 0
                total_kept_faces = 0

                batch_faces = 100000
                total_batches = max(1, (n_faces + batch_faces - 1) // batch_faces)
                face_offset = header_offset + n_verts * 12

                with open(textured_mesh, 'rb') as f:
                    f.seek(face_offset)
                    for batch_index in range(total_batches):
                        with cancel_lock:
                            if cancel_requested:
                                raise PipelineCancelledError("Mission cancelled by user")
                        batch_count = min(batch_faces, n_faces - batch_index * batch_faces)
                        face_data = np.fromfile(f, dtype=face_dtype, count=batch_count)
                        if len(face_data) == 0:
                            break
                        valid = (face_data['n_vert'] == 3) & (face_data['n_tc'] == 6)
                        if not np.any(valid):
                            continue

                        tri_indices = np.column_stack([face_data['v0'][valid], face_data['v1'][valid], face_data['v2'][valid]])
                        tri_positions = local_vertices[tri_indices]
                        edge_1 = tri_positions[:, 1] - tri_positions[:, 0]
                        edge_2 = tri_positions[:, 2] - tri_positions[:, 0]
                        normals = np.cross(edge_1, edge_2)
                        normal_mag = np.linalg.norm(normals, axis=1)
                        normal_cos = normals[:, 2] / np.maximum(normal_mag, 1e-8)
                        if require_upward:
                            keep = (normal_mag > 1e-8) & (normal_cos >= min_normal_cos)
                        else:
                            keep = (normal_mag > 1e-8) & (np.abs(normal_cos) >= min_normal_cos)
                        total_candidate_faces += tri_positions.shape[0]
                        total_kept_faces += int(np.count_nonzero(keep))
                        if not np.any(keep):
                            continue
                        tri_indices = tri_indices[keep]
                        tri_positions = tri_positions[keep]
                        tri_uvs = np.column_stack([
                            face_data['u0'][valid], face_data['t0'][valid],
                            face_data['u1'][valid], face_data['t1'][valid],
                            face_data['u2'][valid], face_data['t2'][valid],
                        ]).reshape(-1, 3, 2)[keep]
                        tri_positions = torch.from_numpy(tri_positions.astype(np.float32)).to(device=device)
                        tri_uvs = torch.from_numpy(tri_uvs.astype(np.float32)).to(device=device)
                        tri_uvs[..., 1] = 1.0 - tri_uvs[..., 1]

                        clip = torch.empty((tri_positions.shape[0], 3, 4), dtype=torch.float32, device=device)
                        clip[..., 0] = tri_positions[..., 0] / max(float(width_m), 1e-6) * 2.0 - 1.0
                        clip[..., 1] = tri_positions[..., 1] / max(float(height_m), 1e-6) * 2.0 - 1.0
                        clip[..., 2] = 1.0 - 2.0 * ((tri_positions[..., 2] - float(min_z)) / max(float(max_z - min_z), 1e-6))
                        clip[..., 3] = 1.0

                        pos = clip.reshape(-1, 4).contiguous()
                        uv_attr = tri_uvs.reshape(-1, 2).contiguous()
                        tri = torch.arange(pos.shape[0], dtype=torch.int32, device=device).reshape(-1, 3)

                        rast, _ = dr.rasterize(glctx, pos.unsqueeze(0), tri, resolution=[raster_h, raster_w])
                        texc, _ = dr.interpolate(uv_attr.unsqueeze(0), rast, tri)
                        batch_color = dr.texture(texture, texc, filter_mode=texture_filter_mode, **texture_sampling_kwargs)[0].to(dtype=torch.float16)
                        batch_depth = rast[0, :, :, 2]
                        batch_mask = rast[0, :, :, 3] > 0
                        nearer = batch_mask & (batch_depth < final_depth)
                        final_color[nearer] = batch_color[nearer]
                        final_depth[nearer] = batch_depth[nearer]

                        del tri_positions, tri_uvs, clip, pos, uv_attr, tri, rast, texc, batch_color, batch_depth, batch_mask, nearer
                        torch.cuda.empty_cache()

                        if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                            report_progress(vol_id, "ORTHO", 97, log=f"CUDA rasterizing mesh: batch {batch_index + 1}/{total_batches}")

                report_progress(vol_id, "ORTHO", 97, log="Reading CUDA rasterizer output...")
                img = (final_color.clamp(0.0, 1.0).mul(255.0).byte().cpu().numpy())
                img = np.flipud(img)
                gpu_filled = np.count_nonzero(np.any(img > 0, axis=2))
                gpu_fill_ratio = gpu_filled / float(raster_w * raster_h)
                if gpu_fill_ratio < 0.005:
                    raise RuntimeError(f"CUDA rasterizer nearly empty ({100.0 * gpu_fill_ratio:.2f}% filled)")
                img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
                write_geotiff(img)
                if total_candidate_faces > 0:
                    mode = "n_z" if require_upward else "|n_z|"
                    report_progress(vol_id, "ORTHO", 97, log=f"CUDA mesh face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces ({mode}>={min_normal_cos:.2f})")
                report_progress(vol_id, "ORTHO", 97, log=f"CUDA gap-fill: {100.0 * fill_before / (raster_w * raster_h):.1f}% -> {100.0 * fill_after / (raster_w * raster_h):.1f}%")
                report_progress(vol_id, "ORTHO", 98, log=f"Mesh ortho written with CUDA rasterizer: {raster_w}x{raster_h}px @ {resolution:.3f}m/px, fill={100.0 * fill_after / (raster_w * raster_h):.1f}%")
                return
            except Exception as gpu_error:
                raise RuntimeError(f"CUDA rasterizer unavailable ({gpu_error})")

        face_offset = header_offset + n_verts * 12
        img = np.zeros((raster_h, raster_w, 3), dtype=np.uint8)
        z_buf = np.full((raster_h, raster_w), -np.inf, dtype=np.float32)
        texture = np.array(PILImage.open(texture_path).convert('RGB'))
        tex_h, tex_w = texture.shape[:2]
        barycentric_samples = np.array([
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.5, 0.5, 0.0],
            [0.5, 0.0, 0.5],
            [0.0, 0.5, 0.5],
            [1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0],
        ], dtype=np.float32)

        batch_faces = 500000
        total_batches = max(1, (n_faces + batch_faces - 1) // batch_faces)
        total_candidate_faces = 0
        total_kept_faces = 0
        with open(textured_mesh, 'rb') as f:
            f.seek(face_offset)
            for batch_index in range(total_batches):
                with cancel_lock:
                    if cancel_requested:
                        raise PipelineCancelledError("Mission cancelled by user")
                batch_count = min(batch_faces, n_faces - batch_index * batch_faces)
                face_data = np.fromfile(f, dtype=face_dtype, count=batch_count)
                if len(face_data) == 0:
                    break
                valid = (face_data['n_vert'] == 3) & (face_data['n_tc'] == 6)
                if not np.any(valid):
                    continue
                tri_indices = np.column_stack([face_data['v0'][valid], face_data['v1'][valid], face_data['v2'][valid]])
                tri_positions = local_vertices[tri_indices]
                edge_1 = tri_positions[:, 1] - tri_positions[:, 0]
                edge_2 = tri_positions[:, 2] - tri_positions[:, 0]
                normals = np.cross(edge_1, edge_2)
                normal_mag = np.linalg.norm(normals, axis=1)
                normal_cos = normals[:, 2] / np.maximum(normal_mag, 1e-8)
                if require_upward:
                    keep = (normal_mag > 1e-8) & (normal_cos >= min_normal_cos)
                else:
                    keep = (normal_mag > 1e-8) & (np.abs(normal_cos) >= min_normal_cos)
                total_candidate_faces += tri_positions.shape[0]
                total_kept_faces += int(np.count_nonzero(keep))
                if not np.any(keep):
                    continue
                tri_indices = tri_indices[keep]
                tri_positions = tri_positions[keep]
                tri_uvs = np.column_stack([
                    face_data['u0'][valid], face_data['t0'][valid],
                    face_data['u1'][valid], face_data['t1'][valid],
                    face_data['u2'][valid], face_data['t2'][valid],
                ]).reshape(-1, 3, 2)[keep]

                for weights in barycentric_samples:
                    pts = np.sum(tri_positions * weights[np.newaxis, :, np.newaxis], axis=1)
                    uv = np.sum(tri_uvs * weights[np.newaxis, :, np.newaxis], axis=1)
                    px = (pts[:, 0] / resolution).astype(np.int32)
                    py = ((height_m - pts[:, 1]) / resolution).astype(np.int32)
                    valid_px = (px >= 0) & (px < raster_w) & (py >= 0) & (py < raster_h)
                    if not np.any(valid_px):
                        continue
                    px = px[valid_px]
                    py = py[valid_px]
                    pz = pts[valid_px, 2]
                    uv = uv[valid_px]
                    tx = np.clip((uv[:, 0] * tex_w).astype(np.int32), 0, tex_w - 1)
                    ty = np.clip(((1.0 - uv[:, 1]) * tex_h).astype(np.int32), 0, tex_h - 1)
                    colors = texture[ty, tx]
                    closer = pz > z_buf[py, px]
                    img[py[closer], px[closer]] = colors[closer]
                    z_buf[py[closer], px[closer]] = pz[closer]

                if batch_index == 0 or (batch_index + 1) % 10 == 0 or batch_index + 1 == total_batches:
                    report_progress(vol_id, "ORTHO", 97, log=f"CPU mesh sampling: batch {batch_index + 1}/{total_batches}")

        filled = np.any(img > 0, axis=2).sum()
        total = raster_w * raster_h
        if total_candidate_faces > 0:
            mode = "n_z" if require_upward else "|n_z|"
            report_progress(vol_id, "ORTHO", 97, log=f"CPU mesh face filter kept {total_kept_faces:,}/{total_candidate_faces:,} faces ({mode}>={min_normal_cos:.2f})")
        report_progress(vol_id, "ORTHO", 97, log=f"CPU mesh fill: {100.0 * filled / total:.1f}%")
        img, fill_before, fill_after = apply_iterative_gap_fill(img, passes=gap_fill_passes)
        report_progress(vol_id, "ORTHO", 97, log=f"CPU gap-fill: {100.0 * fill_before / total:.1f}% -> {100.0 * fill_after / total:.1f}%")

        write_geotiff(img)
        report_progress(vol_id, "ORTHO", 98, log=f"Mesh ortho written with CPU mesh sampling: {raster_w}x{raster_h}px @ {resolution:.3f}m/px")

    except Exception as e:
        report_progress(vol_id, "ORTHO", 97, log=f"Mesh rasterization failed ({e}), falling back to PLY projection...")
        tf = os.path.join(workspace_dir, "alignment_transform.json") if workspace_dir else None
        generate_ortho_from_ply(fused_ply, ortho_file, utm_crs, vol_id, transform_file=tf)


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
        gps_done = os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0
        if gps_done:
            report_progress(vol_id, "GPS_EXTRACTION", 12, log="Existing GPS data found, skipping extraction and inferring UTM CRS...")
            # We still need the UTM CRS for the ortho step
            utm_crs = read_saved_utm_crs(geo_data_file)
            images = [f for f in os.listdir(clean_images_dir) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
            if utm_crs is None and images:
                import pyproj
                with open(os.path.join(clean_images_dir, images[0]), 'rb') as src:
                    try:
                        exif_img = ExifImage(src)
                        if hasattr(exif_img, 'gps_latitude') and hasattr(exif_img, 'gps_longitude'):
                            lat = exif_img.gps_latitude[0] + exif_img.gps_latitude[1]/60 + exif_img.gps_latitude[2]/3600
                            if getattr(exif_img, 'gps_latitude_ref', 'N') == 'S': lat = -lat
                            lon = exif_img.gps_longitude[0] + exif_img.gps_longitude[1]/60 + exif_img.gps_longitude[2]/3600
                            if getattr(exif_img, 'gps_longitude_ref', 'E') == 'W': lon = -lon
                            zone_number = int((lon + 180) / 6) + 1
                            is_south = lat < 0
                            utm_crs = f"EPSG:32{'7' if is_south else '6'}{zone_number:02d}"
                    except Exception: pass
            save_utm_crs(geo_data_file, utm_crs)
        else:
            utm_crs = extract_gps_data(clean_images_dir, geo_data_file, vol_id)

        dense_path = os.path.join(workspace_dir, "dense")
        fused_path = os.path.join(dense_path, "fused.ply")
        textured_dir = os.path.join(dense_path, "textured")
        textured_mesh_path = os.path.join(textured_dir, "mesh.ply")
        textured_texture_path = os.path.join(textured_dir, "texture.png")
        align_tf = os.path.join(workspace_dir, "alignment_transform.json")
        align_tf = align_tf if os.path.exists(align_tf) else None
        ortho_only_ready = params["use_mesh_ortho"] and os.path.exists(textured_mesh_path) and os.path.exists(textured_texture_path)
        if ortho_only_ready:
            report_progress(vol_id, "PREPARING", 13, log="Existing textured mesh found. Skipping SfM/MVS/fusion and rebuilding orthomosaic only.")

        # --- 3. SfM: Feature Extraction ---
        sparse_done = os.path.exists(os.path.join(sparse_path, "0", "cameras.bin")) or os.path.exists(os.path.join(sparse_path, "0", "cameras.txt"))

        if not sparse_done and not ortho_only_ready:
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
                    "--FeatureExtraction.max_image_size", params["feature_max_image_size"],
                    "--AlikedExtraction.max_num_features", params["feature_max_num_features"],
                ]
            else:
                feat_cmd += [
                    "--FeatureExtraction.use_gpu", "1",
                    "--FeatureExtraction.max_image_size", params["feature_max_image_size"],
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

        # --- 7. MVS ---
        # IMPORTANT: MVS must run on the NON-geo-aligned sparse model (sparse/0).
        # The geo-aligned model has huge UTM coordinates (T ~ millions) which cause
        # float32 precision loss in PatchMatch CUDA, making geometric consistency
        # filtering reject all pixels. Geo-alignment is applied AFTER fusion.
        if ortho_only_ready:
            report_progress(vol_id, "STEREO_MVS", 75, log="Existing textured mesh found. Skipping undistortion, PatchMatch, and fusion.")
        else:

            # --- Smart resume: nuke dense dir if fused.ply is empty/invalid ---
            # The entire dense/ must be rebuilt because the sparse model inside it
            # may have been produced from the wrong coordinate system.
            if os.path.exists(fused_path) and os.path.getsize(fused_path) < 100_000:
                report_progress(vol_id, "PREPARING", 71, log="⚠️ Existing fused.ply is empty/invalid (<100KB). Nuking dense/ for full MVS rebuild.")
                shutil.rmtree(dense_path, ignore_errors=True)
            
            # Undistorter — max_image_size must match PatchMatch to avoid
            # resolution mismatch between undistorted model and depth maps.
            if not os.path.exists(os.path.join(dense_path, "stereo", "fusion.cfg")):
                run_command([
                    "colmap", "image_undistorter",
                    "--image_path", clean_images_dir,
                    "--input_path", os.path.join(sparse_path, "0"),
                    "--output_path", dense_path,
                    "--max_image_size", params["mvs_max_image_size"],
                ], vol_id, "UNDISTORT", 70)
            else:
                report_progress(vol_id, "UNDISTORT", 70, log="Undistorted images and fusion.cfg found. Skipping undistortion.")

            # --- PatchMatchStereo ---
            # COLMAP natively handles the photometric pass internally before the geometric pass
            # when geom_consistency is set to 1.
            report_progress(vol_id, "STEREO_MVS", 75, log=f"Running Multi-View Stereo (PatchMatch) gpu_index={params['mvs_gpu_index']} iterations={params['mvs_num_iterations']} samples={params['mvs_num_samples']}")
            run_command([
                "colmap", "patch_match_stereo",
                "--workspace_path", dense_path,
                "--PatchMatchStereo.gpu_index", params["mvs_gpu_index"],
                "--PatchMatchStereo.max_image_size", params["mvs_max_image_size"],
                "--PatchMatchStereo.window_step", params["mvs_window_step"],
                "--PatchMatchStereo.num_iterations", params["mvs_num_iterations"],
                "--PatchMatchStereo.num_samples", params["mvs_num_samples"],
                "--PatchMatchStereo.geom_consistency", "1",
                "--PatchMatchStereo.filter", "1",
                "--PatchMatchStereo.filter_min_num_consistent", params["mvs_filter_min_num_consistent"],
            ], vol_id, "STEREO_MVS", 75)

            # Stereo Fusion — uses geometric depth maps.
            # Explicit --input_type geometric is required for a successful multi-point fusion.
            # Clamp min_num_pixels to num_images+1 (COLMAP 4 AutomaticReconstruction behavior)
            # to avoid requiring more consistent observations than images exist.
            num_undistorted = len([f for f in os.listdir(os.path.join(dense_path, "images"))
                                   if f.lower().endswith(('.jpg', '.jpeg', '.png'))]) if os.path.isdir(os.path.join(dense_path, "images")) else 0
            effective_min_num_pixels = min(int(params["fusion_min_num_pixels"]), num_undistorted + 1) if num_undistorted > 0 else int(params["fusion_min_num_pixels"])
            report_progress(vol_id, "FUSION", 90, log=f"Fusion: {num_undistorted} undistorted images, min_num_pixels={effective_min_num_pixels}")
            if not os.path.exists(fused_path):
                run_command([
                    "colmap", "stereo_fusion",
                    "--workspace_path", dense_path,
                    "--input_type", "geometric",
                    "--output_path", fused_path,
                    "--StereoFusion.max_image_size", params["fusion_max_image_size"],
                    "--StereoFusion.cache_size", params["fusion_cache_size"],
                    "--StereoFusion.min_num_pixels", str(effective_min_num_pixels),
                ], vol_id, "FUSION", 92)
            else:
                report_progress(vol_id, "FUSION", 92, log="Fused point cloud found. Skipping stereo fusion.")
        
        # --- 8b. Geo-alignment of dense model ---
        # Now that fusion is done in COLMAP coords, align the fused result to UTM
        # for geo-referenced orthomosaic generation.
        sparse_geo_path = os.path.join(workspace_dir, "sparse_geo")
        fused_geo_path = os.path.join(dense_path, "fused_geo.ply")
        
        if not ortho_only_ready and os.path.exists(fused_path) and os.path.getsize(fused_path) >= 100_000:
            if os.path.exists(geo_data_file) and os.path.getsize(geo_data_file) > 0 and not os.path.exists(fused_geo_path):
                # Align the dense sparse model to UTM using GPS reference
                os.makedirs(sparse_geo_path, exist_ok=True)
                align_done = os.path.exists(os.path.join(sparse_geo_path, "cameras.bin"))
                if not align_done:
                    run_command([
                        "colmap", "model_aligner",
                        "--input_path", os.path.join(sparse_path, "0"),
                        "--output_path", sparse_geo_path,
                        "--ref_images_path", geo_data_file,
                        "--ref_is_gps", "0",
                        "--alignment_max_error", "0.2"
                    ], vol_id, "ALIGNING", 93)
                
                # Transform fused.ply to geo-referenced coordinates using pycolmap
                report_progress(vol_id, "ALIGNING", 94, log="Geo-referencing fused point cloud...")
                try:
                    import pycolmap
                    # Read both reconstructions to compute the alignment transform
                    rec_src = pycolmap.Reconstruction(os.path.join(sparse_path, "0"))
                    rec_dst = pycolmap.Reconstruction(sparse_geo_path)
                    
                    # Compute Sim3 from source to destination using shared image positions
                    src_centers = {}
                    for image_id in rec_src.images:
                        img = rec_src.images[image_id]
                        src_centers[img.name] = img.projection_center()
                    
                    dst_centers = {}
                    for image_id in rec_dst.images:
                        img = rec_dst.images[image_id]
                        dst_centers[img.name] = img.projection_center()
                    
                    # Find common images and compute transform
                    common = set(src_centers.keys()) & set(dst_centers.keys())
                    if len(common) >= 3:
                        src_pts = np.array([src_centers[n] for n in sorted(common)])
                        dst_pts = np.array([dst_centers[n] for n in sorted(common)])
                        
                        # Estimate Sim3 via Umeyama
                        src_mean = src_pts.mean(axis=0)
                        dst_mean = dst_pts.mean(axis=0)
                        src_c = src_pts - src_mean
                        dst_c = dst_pts - dst_mean
                        
                        src_var = np.sum(src_c ** 2) / len(common)
                        H = dst_c.T @ src_c / len(common)
                        U, S, Vt = np.linalg.svd(H)
                        d = np.linalg.det(U) * np.linalg.det(Vt)
                        D = np.diag([1, 1, 1 if d > 0 else -1])
                        R = U @ D @ Vt
                        scale = np.sum(S * np.diag(D)) / src_var
                        t = dst_mean - scale * R @ src_mean
                        
                        # Apply transform to fused.ply
                        from plyfile import PlyData, PlyElement
                        plydata = PlyData.read(fused_path)
                        vertices = plydata['vertex']
                        xyz = np.column_stack([vertices['x'], vertices['y'], vertices['z']]).astype(np.float64)
                        nxyz = np.column_stack([vertices['nx'], vertices['ny'], vertices['nz']]).astype(np.float64)
                        
                        xyz_geo = (scale * (R @ xyz.T) + t[:, np.newaxis]).T
                        nxyz_geo = (R @ nxyz.T).T
                        
                        # Write geo-referenced PLY
                        new_vertices = vertices.data.copy()
                        new_vertices['x'] = xyz_geo[:, 0].astype(np.float32)
                        new_vertices['y'] = xyz_geo[:, 1].astype(np.float32)
                        new_vertices['z'] = xyz_geo[:, 2].astype(np.float32)
                        new_vertices['nx'] = nxyz_geo[:, 0].astype(np.float32)
                        new_vertices['ny'] = nxyz_geo[:, 1].astype(np.float32)
                        new_vertices['nz'] = nxyz_geo[:, 2].astype(np.float32)
                        
                        el = PlyElement.describe(new_vertices, 'vertex')
                        PlyData([el], text=False).write(fused_geo_path)
                        report_progress(vol_id, "ALIGNING", 94, log=f"Geo-referenced {len(xyz)} points (scale={scale:.4f})")
                        
                        # Save transform for mesh ortho path
                        transform_file = os.path.join(workspace_dir, "alignment_transform.json")
                        with open(transform_file, 'w') as tf:
                            json.dump({"R": R.tolist(), "scale": scale, "t": t.tolist()}, tf)
                    else:
                        report_progress(vol_id, "ALIGNING", 94, log="Not enough common images for alignment, using raw fused.ply")
                        fused_geo_path = fused_path
                except Exception as e:
                    report_progress(vol_id, "ALIGNING", 94, log=f"Geo-referencing failed ({e}), using raw fused.ply")
                    fused_geo_path = fused_path
            elif os.path.exists(fused_geo_path):
                report_progress(vol_id, "ALIGNING", 94, log="Geo-referenced point cloud found. Skipping alignment.")
            else:
                fused_geo_path = fused_path
        elif ortho_only_ready:
            report_progress(vol_id, "ALIGNING", 94, log="Existing textured mesh found. Skipping dense geo-alignment and using any saved transform if present.")
        
        # --- 9. Orthomosaic Generation ---
        # Always use fused.ply (COLMAP coords) + alignment transform for ortho.
        # The Sim3 transform is applied in float64 during rasterization to avoid
        # UTM float32 precision loss (which causes striping at Y~4.7M).
        ortho_file = os.path.join(workspace_dir, "orthomosaic.tif")
        if ortho_only_ready or (os.path.exists(fused_path) and os.path.getsize(fused_path) >= 100_000):
            try:
                if params["use_mesh_ortho"]:
                    generate_ortho_from_mesh(dense_path, ortho_file, utm_crs, vol_id, workspace_dir=workspace_dir, params=params)
                else:
                    generate_ortho_from_ply(fused_path, ortho_file, utm_crs, vol_id, transform_file=align_tf)
            except Exception as e:
                report_progress(vol_id, "ORTHO", 95, log=f"Error generating ortho: {e}. Using dummy.")
                generate_dummy_ortho(ortho_file)
        else:
            report_progress(vol_id, "ORTHO", 95, log="fused.ply not found or empty. Creating dummy ortho.")
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


def worker_main():
    global current_mission_id, cancel_requested

    threading.Thread(target=control_consumer_thread, daemon=True).start()
    consumer = create_consumer()

    print("🎧 App 1 (COLMAP 4 — ALIKED/GLOMAP + Legacy Fallback) ready.")

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue

            try:
                val = msg.value().decode('utf-8')
                mission = json.loads(val)
                vol_id = mission['vol_id']

                with cancel_lock:
                    current_mission_id = vol_id
                    cancel_requested = False

                def make_host_path(path):
                    if path.startswith("/host"):
                        return path
                    if not path.startswith("/"):
                        path = "/" + path
                    return "/host" + path

                ws_base = mission.get('workspace_dir', DEFAULT_WORKSPACE_ROOT)
                work_dir = make_host_path(resolve_workspace_dir(ws_base, vol_id))
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


if __name__ == "__main__":
    worker_main()
