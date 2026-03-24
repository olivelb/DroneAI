import os
import json
import logging
import sys
import threading
import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.warp import transform as warp_transform
import cv2
from confluent_kafka import Consumer, Producer
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_IMAGE_TILES, TOPIC_ORTHO, TOPIC_STATUS, TOPIC_TILE_DETECTIONS

# --- CONFIGURATION KAFKA ---
TOPIC_IN_ORTHO = TOPIC_ORTHO
TOPIC_OUT_TILES = TOPIC_IMAGE_TILES
TOPIC_IN_DETECTIONS = TOPIC_TILE_DETECTIONS

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app3-processing")

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'processing-group',
    'auto.offset.reset': 'earliest',
    'max.poll.interval.ms': 7200000 # 2 hours
})
consumer.subscribe([TOPIC_IN_ORTHO, TOPIC_IN_DETECTIONS])

producer = Producer({'bootstrap.servers': KAFKA_BROKER})


class MissionRegistry:
    def __init__(self):
        self._missions = {}
        self._lock = threading.RLock()

    def get(self, vol_id):
        with self._lock:
            return self._missions.get(vol_id)

    def set(self, vol_id, mission):
        with self._lock:
            self._missions[vol_id] = mission

    def set_total_tiles(self, vol_id, total_tiles):
        with self._lock:
            mission = self._missions.get(vol_id)
            if mission is not None:
                mission['total_tiles'] = total_tiles

    def record_tile_detections(self, vol_id, tile_index, detections):
        with self._lock:
            mission = self._missions.get(vol_id)
            if mission is None:
                return None, False
            mission['detections'].extend(detections)
            mission['received_tiles'].add(tile_index)
            total = mission.get('total_tiles')
            is_complete = total is not None and len(mission['received_tiles']) == total
            return mission, is_complete

    def pop(self, vol_id):
        with self._lock:
            return self._missions.pop(vol_id, None)


missions = MissionRegistry()


def resolve_host_path(path_value):
    if path_value.startswith("/host"):
        return path_value
    if not path_value.startswith("/"):
        path_value = "/" + path_value
    return "/host" + path_value


def cleanup_tiles_directory(tiles_dir):
    for entry in os.listdir(tiles_dir):
        if entry.startswith("tile_") and entry.lower().endswith(".jpg"):
            try:
                os.remove(os.path.join(tiles_dir, entry))
            except OSError as error:
                logger.warning("Failed to remove stale tile %s: %s", entry, error)


def build_tile_starts(full_size, tile_size, overlap):
    if full_size <= tile_size:
        return [0]
    stride = max(1, tile_size - overlap)
    starts = list(range(0, max(full_size - tile_size, 0) + 1, stride))
    last_start = full_size - tile_size
    if starts[-1] != last_start:
        starts.append(last_start)
    return starts

def report_progress(vol_id, step, progress, status="processing", log=None):
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "TILER"}
    if log:
        msg["log"] = log
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()


def resolve_detection_gps(det, mission):
    lat = det.get('geo_lat')
    lon = det.get('geo_lon')
    if lat is not None and lon is not None:
        try:
            lat = float(lat)
            lon = float(lon)
            if np.isfinite(lat) and np.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                return lat, lon
        except (TypeError, ValueError):
            pass

    ortho_transform = mission.get('transform')
    ortho_crs = mission.get('crs')
    if not ortho_transform or not ortho_crs or ortho_crs == "unknown":
        return None

    try:
        gx = float(det['global_pixel_x'])
        gy = float(det['global_pixel_y'])
        c, a, b, f, d, e = ortho_transform
        proj_x = c + a * gx + b * gy
        proj_y = f + d * gx + e * gy
        lon_arr, lat_arr = warp_transform(ortho_crs, "EPSG:4326", [proj_x], [proj_y])
        lat = float(lat_arr[0])
        lon = float(lon_arr[0])
        if np.isfinite(lat) and np.isfinite(lon) and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            return lat, lon
    except Exception as error:
        logger.debug("Failed to project detection GPS for %s: %s", det.get('vol_id', 'unknown'), error)
        return None

    return None


def format_detection_gps(det, mission):
    gps = resolve_detection_gps(det, mission)
    if gps is None:
        return None
    lat, lon = gps
    return [f"lat {lat:.6f}", f"lon {lon:.6f}"]


def draw_detection_label(img, anchor_x, anchor_y, lines):
    if not lines:
        return

    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.55
    font_thickness = 1
    line_gap = 6
    padding_x = 8
    padding_y = 6
    margin = 8

    line_sizes = [cv2.getTextSize(line, font, font_scale, font_thickness)[0] for line in lines]
    text_width = max(size[0] for size in line_sizes)
    line_height = max(size[1] for size in line_sizes)
    box_width = text_width + padding_x * 2
    box_height = len(lines) * line_height + (len(lines) - 1) * line_gap + padding_y * 2

    candidate_positions = [
        (anchor_x + 12, anchor_y - box_height - 12),
        (anchor_x + 12, anchor_y + 12),
        (anchor_x - box_width - 12, anchor_y - box_height - 12),
        (anchor_x - box_width - 12, anchor_y + 12),
    ]
    preferred_x, preferred_y = candidate_positions[0]
    for cand_x, cand_y in candidate_positions:
        if margin <= cand_x <= img.shape[1] - box_width - margin and margin <= cand_y <= img.shape[0] - box_height - margin:
            preferred_x, preferred_y = cand_x, cand_y
            break
    box_x = min(max(margin, preferred_x), max(margin, img.shape[1] - box_width - margin))
    box_y = min(max(margin, preferred_y), max(margin, img.shape[0] - box_height - margin))

    cv2.line(img, (anchor_x, anchor_y), (box_x, box_y + box_height // 2), (255, 255, 255), 1, cv2.LINE_AA)
    overlay = img.copy()
    cv2.rectangle(overlay, (box_x, box_y), (box_x + box_width, box_y + box_height), (0, 0, 0), -1)
    roi = img[box_y:box_y + box_height, box_x:box_x + box_width]
    overlay_roi = overlay[box_y:box_y + box_height, box_x:box_x + box_width]
    cv2.addWeighted(overlay_roi, 0.60, roi, 0.40, 0, roi)
    cv2.rectangle(img, (box_x, box_y), (box_x + box_width, box_y + box_height), (255, 255, 255), 1)

    baseline_y = box_y + padding_y + line_height
    for index, line in enumerate(lines):
        text_y = baseline_y + index * (line_height + line_gap)
        text_origin = (box_x + padding_x, text_y)
        cv2.putText(img, line, text_origin, font, font_scale, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, line, text_origin, font, font_scale, (255, 255, 0), font_thickness, cv2.LINE_AA)

def generate_final_ortho(vol_id, mission):
    ortho_path = mission['ortho_path']
    base, ext = os.path.splitext(ortho_path)
    output_path = f"{base}_annotated{ext}"
    
    report_progress(vol_id, "FINAL_IMAGE", 90, log="Generating annotated orthomosaic...")
    try:
        with rasterio.open(ortho_path) as src:
            meta = src.meta.copy()
            data = src.read()
            
            # Data is usually (C, H, W). Rasterio expects RGB, we need HWC for OpenCV
            img = data[:3].transpose(1, 2, 0).copy() # C,H,W -> H,W,C
            
            # Dessiner les masques
            for det in mission['detections']:
                if 'segment' in det and len(det['segment']) > 0:
                    pts = np.array(det['segment'], np.int32)
                    pts = pts.reshape((-1, 1, 2))
                    
                    # Draw a transparent red overlay for the mask
                    overlay = img.copy()
                    cv2.fillPoly(overlay, [pts], (255, 0, 0)) # Red in RGB
                    cv2.addWeighted(overlay, 0.4, img, 0.6, 0, img)
                    
                    # Draw the contour
                    cv2.polylines(img, [pts], True, (255, 0, 0), 2)
                    
                    # Draw center point / label
                    cx, cy = int(det['global_pixel_x']), int(det['global_pixel_y'])
                    cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)
                    gps_lines = format_detection_gps(det, mission)
                    draw_detection_label(img, cx, cy, gps_lines)
                    
                    # Log to DB/File later for dashboard
            
            # Back to C,H,W
            out_data = img.transpose(2, 0, 1)
            
            with rasterio.open(output_path, 'w', **meta) as dst:
                dst.write(out_data)
                
        report_progress(vol_id, "DONE", 100, status="success", log=f"Annotated orthomosaic saved to {output_path}")
        
    except Exception as e:
        logger.exception("Failed to generate final image for %s", vol_id)
        report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to generate final image: {e}")

def slice_orthomosaic(ortho_path, vol_id, tile_size=1024, classes=["car"], ai_confidence=0.3):
    """Découpe un GeoTIFF en tuiles et les envoie sur Kafka."""
    ortho_path = resolve_host_path(ortho_path)

    # By default, write tiles inside the same mission workspace as the ortho.
    # An explicit TILES_BASE_DIR still overrides this behavior when needed.
    tiles_base = os.getenv("TILES_BASE_DIR")
    if tiles_base:
        if tiles_base.startswith("/host"):
            tiles_dir = os.path.join(tiles_base, vol_id)
        else:
            tiles_dir = os.path.join("/host", tiles_base.lstrip("/"), vol_id)
    else:
        tiles_dir = os.path.join(os.path.dirname(ortho_path), "tiles")
    os.makedirs(tiles_dir, exist_ok=True)
    cleanup_tiles_directory(tiles_dir)
    
    report_progress(vol_id, "TILING_START", 0)
    
    if not os.path.exists(ortho_path):
        report_progress(vol_id, "ERROR", 0, status="error", log=f"File not found: {ortho_path}")
        return

    try:
        with rasterio.open(ortho_path) as src:
            width = src.width
            height = src.height
            tile_overlap = max(0, min(tile_size // 2, int(os.getenv("TILE_OVERLAP", str(tile_size // 4)))))
            x_starts = build_tile_starts(width, tile_size, tile_overlap)
            y_starts = build_tile_starts(height, tile_size, tile_overlap)
            
            transform_list = list(src.transform.to_gdal()) if src.transform else None
            crs_str = src.crs.to_string() if src.crs else "unknown"
            
            missions.set(vol_id, {
                "ortho_path": ortho_path,
                "transform": transform_list,
                "crs": crs_str,
                "tiles_count": 0,
                "detections": [],
                "received_tiles": set()
            })
            
            # Calculer le nombre total de tuiles pour le calcul du progrès
            total_cols = len(x_starts)
            total_rows = len(y_starts)
            total_tiles = total_cols * total_rows
            
            # Set total_tiles BEFORE producing any tile messages to avoid
            # race condition where detections arrive before count is known (BUG 1)
            missions.set_total_tiles(vol_id, total_tiles)
            
            tile_index = 0
            report_progress(vol_id, "TILING_START", 0, log=f"Writing {total_tiles} overlapping tiles (size={tile_size}, overlap={tile_overlap})")

            for y in y_starts:
                for x in x_starts:
                    window = Window(x, y, min(tile_size, width - x), min(tile_size, height - y))
                    
                    tile_data = src.read(window=window)
                    tile_meta = src.meta.copy()
                    
                    # Force 3-band RGB for JPEG if it has more, or use PNG/WEBP
                    driver = "JPEG"
                    if src.count > 3:
                        tile_data = tile_data[:3, :, :]
                    elif src.count == 1:
                        # Convert 1-band (grayscale) to 3-band (RGB) for JPEG compatibility
                        tile_data = np.repeat(tile_data, 3, axis=0)
                        
                    tile_meta.update({
                        "driver": driver,
                        "height": window.height,
                        "width": window.width,
                        "transform": src.window_transform(window),
                        "count": 3
                    })
                    
                    tile_filename = f"tile_{tile_index}.jpg"
                    tile_path = os.path.join(tiles_dir, tile_filename)
                    
                    with rasterio.open(tile_path, 'w', **tile_meta) as dst:
                        dst.write(tile_data)
                    
                    # Store path WITHOUT /host for external services if they use a different mount or absolute host paths
                    # But here we keep it consistent.
                    tile_msg = {
                        "vol_id": vol_id,
                        "tile_index": tile_index,
                        "tile_path": tile_path.replace("/host", ""), # Return path as seen by host
                        "offset_x": x,
                        "offset_y": y,
                        "classes": classes,
                        "ai_confidence": ai_confidence,
                        "total_tiles": total_tiles,
                        "ortho_transform": transform_list,
                        "ortho_crs": crs_str
                    }
                    producer.produce(TOPIC_OUT_TILES, key=f"{vol_id}_{tile_index}", value=json.dumps(tile_msg))
                    
                    tile_index += 1
                    progress = int((tile_index / total_tiles) * 100)
                    if tile_index % 10 == 0:
                        report_progress(vol_id, "TILING_IN_PROGRESS", progress)
            
            # Update to exact count (may differ from estimate if loop was interrupted)
            missions.set_total_tiles(vol_id, tile_index)
            producer.flush()
            report_progress(vol_id, "TILING_DONE", 100, status="success")
            print(f"📦 Orthomosaïque découpée en {tile_index} tuiles pour le vol {vol_id}.")
            
    except Exception as e:
        logger.exception("Failed to tile orthomosaic for %s", vol_id)
        error_msg = f"Failed to open orthomosaic: {str(e)}"
        try:
            if "simulée" in open(ortho_path, "r", errors="ignore").read():
                error_msg = "Cannot tile a simulated text file. Need a real GeoTIFF."
        except Exception:
            pass
        report_progress(vol_id, "ERROR", 0, status="error", log=error_msg)
        print(f"❌ {error_msg}")

print("🎧 App 3 (Tiler/Aggregator) en attente...")

try:
    while True:
        msg = consumer.poll(1.0)
        if msg is None: continue
        if msg.error(): continue
        
        data = json.loads(msg.value().decode('utf-8'))
        topic = msg.topic()
        
        if topic == TOPIC_IN_ORTHO:
            vol_id = data['vol_id']
            ortho_path = data['ortho_path']
            classes = data.get('classes', ['car'])
            ai_confidence = data.get('ai_confidence', 0.3)
            slice_orthomosaic(ortho_path, vol_id, classes=classes, ai_confidence=ai_confidence)
            
        elif topic == TOPIC_IN_DETECTIONS:
            vol_id = data['vol_id']
            mission, is_complete = missions.record_tile_detections(vol_id, data['tile_index'], data['detections'])
            if mission is not None and is_complete:
                report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
                final_mission = missions.pop(vol_id)
                if final_mission is not None:
                    generate_final_ortho(vol_id, final_mission)

except KeyboardInterrupt:
    pass
finally:
    consumer.close()
