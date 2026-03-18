import os
import json
import numpy as np
import rasterio
from rasterio.windows import Window
import cv2
from confluent_kafka import Consumer, Producer

# --- CONFIGURATION KAFKA ---
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.kafka.svc.cluster.local:9092")
TOPIC_IN_ORTHO = "images-ortho"
TOPIC_OUT_TILES = "image-tiles"
TOPIC_IN_DETECTIONS = "tile-detections"
TOPIC_STATUS = "pipeline-status"

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'processing-group',
    'auto.offset.reset': 'earliest',
    'max.poll.interval.ms': 7200000 # 2 hours
})
consumer.subscribe([TOPIC_IN_ORTHO, TOPIC_IN_DETECTIONS])

producer = Producer({'bootstrap.servers': KAFKA_BROKER})

# État global pour suivre les missions
missions = {}

def report_progress(vol_id, step, progress, status="processing", log=None):
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "TILER"}
    if log:
        msg["log"] = log
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()

def generate_final_ortho(vol_id):
    mission = missions.get(vol_id)
    if not mission: return
    
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
                    
                    # Log to DB/File later for dashboard
            
            # Back to C,H,W
            out_data = img.transpose(2, 0, 1)
            
            with rasterio.open(output_path, 'w', **meta) as dst:
                dst.write(out_data)
                
        report_progress(vol_id, "DONE", 100, status="success", log=f"Annotated orthomosaic saved to {output_path}")
        
    except Exception as e:
        report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to generate final image: {e}")

def slice_orthomosaic(ortho_path, vol_id, tile_size=1024, classes=["car"], ai_confidence=0.3):
    """Découpe un GeoTIFF en tuiles et les envoie sur Kafka."""
    # Ensure ortho_path uses the /host prefix if needed
    if not ortho_path.startswith("/host"):
        if not ortho_path.startswith("/"): ortho_path = "/" + ortho_path
        ortho_path = "/host" + ortho_path

    # Define tiles directory on the host filesystem
    tiles_base = os.getenv("TILES_BASE_DIR", "/home/olivier/workspace/tiles")
    tiles_dir = os.path.join("/host", tiles_base.lstrip("/"), vol_id)
    os.makedirs(tiles_dir, exist_ok=True)
    
    report_progress(vol_id, "TILING_START", 0)
    
    if not os.path.exists(ortho_path):
        report_progress(vol_id, "ERROR", 0, status="error", log=f"File not found: {ortho_path}")
        return

    try:
        with rasterio.open(ortho_path) as src:
            width = src.width
            height = src.height
            
            transform_list = list(src.transform.to_gdal()) if src.transform else None
            crs_str = src.crs.to_string() if src.crs else "unknown"
            
            missions[vol_id] = {
                "ortho_path": ortho_path,
                "transform": transform_list,
                "crs": crs_str,
                "tiles_count": 0,
                "detections": [],
                "received_tiles": set()
            }
            
            # Calculer le nombre total de tuiles pour le calcul du progrès
            total_cols = (width + tile_size - 1) // tile_size
            total_rows = (height + tile_size - 1) // tile_size
            total_tiles = total_cols * total_rows
            
            # Set total_tiles BEFORE producing any tile messages to avoid
            # race condition where detections arrive before count is known (BUG 1)
            missions[vol_id]["total_tiles"] = total_tiles
            
            tile_index = 0
            for y in range(0, height, tile_size):
                for x in range(0, width, tile_size):
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
                        "ortho_transform": transform_list,
                        "ortho_crs": crs_str
                    }
                    producer.produce(TOPIC_OUT_TILES, key=f"{vol_id}_{tile_index}", value=json.dumps(tile_msg))
                    
                    tile_index += 1
                    progress = int((tile_index / total_tiles) * 100)
                    if tile_index % 10 == 0:
                        report_progress(vol_id, "TILING_IN_PROGRESS", progress)
            
            # Update to exact count (may differ from estimate if loop was interrupted)
            missions[vol_id]["total_tiles"] = tile_index
            producer.flush()
            report_progress(vol_id, "TILING_DONE", 100, status="success")
            print(f"📦 Orthomosaïque découpée en {tile_index} tuiles pour le vol {vol_id}.")
            
    except Exception as e:
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
            if vol_id in missions:
                missions[vol_id]['detections'].extend(data['detections'])
                missions[vol_id]['received_tiles'].add(data['tile_index'])
                
                # Check if we have received all tiles (guard against missing key)
                total = missions[vol_id].get('total_tiles')
                if total is not None and len(missions[vol_id]['received_tiles']) == total:
                    report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
                    
                    # Build final annotated image
                    generate_final_ortho(vol_id)
                    
                    # Memory Leak Fix: Clean up the mission state when done
                    del missions[vol_id]

except KeyboardInterrupt:
    pass
finally:
    consumer.close()
