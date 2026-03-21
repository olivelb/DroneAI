import os
import json
import math
import torch
import rasterio
from ultralytics import YOLO
from pyproj import Transformer
from confluent_kafka import Consumer, Producer

# --- CONFIGURATION KAFKA ---
KAFKA_BROKER = os.getenv("KAFKA_BROKER", "my-kafka.kafka.svc.cluster.local:9092")
TOPIC_IN = "image-tiles"
TOPIC_OUT = "tile-detections"
TOPIC_STATUS = "pipeline-status"

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'ia-tile-workers',
    'auto.offset.reset': 'earliest'
})
consumer.subscribe([TOPIC_IN])

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
mission_stats = {}

def report_progress(vol_id, step, progress, status="processing", log=None):
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "IA"}
    if log:
        msg["log"] = log
        print(f"[{step}] {log}")
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()

# --- CHARGEMENT DU MODÈLE IA ---
print("🧠 Chargement du modèle YOLOv11 Segmentation...")
model = YOLO("yolo11n-seg.pt") 
device_type = 'cuda:0' if torch.cuda.is_available() else 'cpu'
print(f"🖥️ Using device: {device_type}")


def compute_inference_imgsz(tile_path):
    with rasterio.open(tile_path) as src:
        tile_max_dim = max(src.width, src.height)
    snapped = int(math.ceil(tile_max_dim / 32.0) * 32)
    return max(960, min(1536, snapped))


def run_detection(tile_path, class_ids, requested_conf):
    base_imgsz = compute_inference_imgsz(tile_path)
    attempts = [
        {
            "conf": requested_conf,
            "imgsz": base_imgsz,
            "augment": False,
            "retina_masks": True,
            "label": f"primary pass imgsz={base_imgsz} conf={requested_conf:.2f}",
        },
        {
            "conf": max(0.10, min(requested_conf, 0.20)),
            "imgsz": min(1536, max(1280, base_imgsz)),
            "augment": True,
            "retina_masks": True,
            "label": "fallback pass with TTA",
        },
    ]

    best_results = []
    best_count = -1
    best_attempt = attempts[0]
    for attempt in attempts:
        results = model.predict(
            source=tile_path,
            classes=class_ids,
            device=device_type,
            conf=attempt["conf"],
            imgsz=attempt["imgsz"],
            augment=attempt["augment"],
            retina_masks=attempt["retina_masks"],
            max_det=100,
            iou=0.45,
            verbose=False,
        )
        count = sum(len(result.boxes) for result in results if result.boxes is not None)
        if count > best_count:
            best_results = results
            best_count = count
            best_attempt = attempt
        if count > 0:
            break
    return best_results, best_attempt, max(best_count, 0)

print("🎧 App 2 (IA Workers) en attente de tuiles sur Kafka...")

try:
    while True:
        msg = consumer.poll(1.0)
        if msg is None: continue
        if msg.error(): continue
            
        # 1. Lecture de la tuile
        tile_info = json.loads(msg.value().decode('utf-8'))
        vol_id = tile_info['vol_id']
        tile_path = tile_info['tile_path']
        req_classes = tile_info.get('classes', ['car'])
        req_conf = float(tile_info.get('ai_confidence', 0.3))
        total_tiles = int(tile_info.get('total_tiles', 0) or 0)
        
        # Mapping simple COCO (ajoutez d'autres classes si besoin)
        COCO_MAP = {
            "person": 0, "bicycle": 1, "car": 2, "motorcycle": 3, "airplane": 4, 
            "bus": 5, "train": 6, "truck": 7, "boat": 8, "traffic light": 9, 
            "fire hydrant": 10, "stop sign": 11, "parking meter": 12, "bench": 13,
            "bird": 14, "cat": 15, "dog": 16, "horse": 17, "sheep": 18, "cow": 19,
            "elephant": 20, "bear": 21, "zebra": 22, "giraffe": 23, "backpack": 24,
            "umbrella": 25, "handbag": 26, "tie": 27, "suitcase": 28, "frisbee": 29
        }
        
        class_ids = [COCO_MAP[c.lower()] for c in req_classes if c.lower() in COCO_MAP]
        if not class_ids:
            class_ids = [2] # fallback on car if unknown
            
        # Ensure path uses /host prefix for container access
        if not tile_path.startswith("/host"):
            if not tile_path.startswith("/"): tile_path = "/" + tile_path
            tile_path = "/host" + tile_path
            
        offset_x = tile_info['offset_x']
        offset_y = tile_info['offset_y']
        
        # We assume the orthomosaic transform and CRS are passed in the message to compute real-world coordinates
        ortho_transform = tile_info.get('ortho_transform')
        ortho_crs = tile_info.get('ortho_crs')
        
        if not os.path.exists(tile_path):
            report_progress(vol_id, "ERROR", 0, status="error", log=f"Tile not found: {tile_path}")
            continue

        stats = mission_stats.setdefault(vol_id, {"processed": 0, "detections": 0, "total_tiles": total_tiles})
        if total_tiles:
            stats["total_tiles"] = total_tiles

        # 2. Inférence YOLO Segmentation
        results, attempt, raw_detection_count = run_detection(tile_path, class_ids, req_conf)
        
        detections = []
        
        # Geolocation transformer setup if CRS is available
        proj_transformer = None
        if ortho_crs and ortho_crs != "unknown":
            try:
                proj_transformer = Transformer.from_crs(ortho_crs, "EPSG:4326", always_xy=True)
            except Exception as e:
                print(f"Failed to create CRS transformer: {e}")

        for result in results:
            if result.boxes is not None and result.masks is not None:
                boxes = result.boxes
                masks = result.masks
                
                # Get the segmentation segments (normalized coordinates or absolute depending on ultralytics version, usually absolute pixel coords)
                segments = masks.xy # This is a list of segments (one for each detection)
                
                for idx, box in enumerate(boxes):
                    # Coordonnées du centre de la bbox locale
                    lx_center = box.xywh[0][0].item()
                    ly_center = box.xywh[0][1].item()
                    
                    # Décalage pour obtenir les coordonnées globales (pixels sur l'ortho entière)
                    gx = lx_center + offset_x
                    gy = ly_center + offset_y
                    
                    # Coordonnées géographiques si transform disponible
                    geo_lat = None
                    geo_lon = None
                    if ortho_transform and proj_transformer:
                        try:
                            # Apply affine transform to get UTM/projected coordinates
                            # ortho_transform is typically [c, a, b, f, d, e] from GDAL
                            c, a, b, f, d, e = ortho_transform
                            # pixel to projected coords
                            proj_x = c + a * gx + b * gy
                            proj_y = f + d * gx + e * gy
                            
                            # projected to lon/lat
                            lon, lat = proj_transformer.transform(proj_x, proj_y)
                            geo_lon = float(lon)
                            geo_lat = float(lat)
                        except Exception as e:
                            pass
                    
                    # Traiter le segment/masque
                    global_segment = []
                    if idx < len(segments):
                        segment = segments[idx]
                        for point in segment:
                            global_segment.append([
                                float(point[0] + offset_x),
                                float(point[1] + offset_y)
                            ])
                    
                    detections.append({
                        "vol_id": vol_id,
                        "global_pixel_x": float(gx),
                        "global_pixel_y": float(gy),
                        "geo_lon": geo_lon,
                        "geo_lat": geo_lat,
                        "confidence": round(float(box.conf[0].item()), 2),
                        "class_id": int(box.cls[0].item()),
                        "segment": global_segment
                    })

        stats["processed"] += 1
        stats["detections"] += len(detections)
        total = stats.get("total_tiles") or stats["processed"]
        progress = min(99, int((stats["processed"] / max(total, 1)) * 100))
        if detections:
            report_progress(vol_id, "DETECTING", progress, log=f"Tile {tile_info['tile_index']} produced {len(detections)} detections via {attempt['label']}")
        elif stats["processed"] == 1 or stats["processed"] % 10 == 0:
            report_progress(vol_id, "DETECTING", progress, log=f"Processed {stats['processed']}/{total} tiles, detections={stats['detections']} ({attempt['label']})")
        
        # 3. Envoi des détections de la tuile à l'agrégateur (App 3)
        tile_result = {
            "vol_id": vol_id,
            "tile_index": tile_info['tile_index'],
            "detections": detections
        }
        producer.produce(TOPIC_OUT, key=str(vol_id), value=json.dumps(tile_result))
        producer.flush()

        if total_tiles and stats["processed"] >= total_tiles:
            summary = f"IA finished {stats['processed']} tiles with {stats['detections']} detections"
            report_progress(vol_id, "DETECTING", 100, status="success", log=summary)
            mission_stats.pop(vol_id, None)

except KeyboardInterrupt:
    print("Arrêt demandé par l'utilisateur.")
finally:
    consumer.close()
