import os
import json
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

        # 2. Inférence YOLO Segmentation
        results = model.predict(source=tile_path, classes=class_ids, device=device_type, conf=req_conf)
        
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
        
        # 3. Envoi des détections de la tuile à l'agrégateur (App 3)
        tile_result = {
            "vol_id": vol_id,
            "tile_index": tile_info['tile_index'],
            "detections": detections
        }
        producer.produce(TOPIC_OUT, key=str(vol_id), value=json.dumps(tile_result))
        producer.flush()

except KeyboardInterrupt:
    print("Arrêt demandé par l'utilisateur.")
finally:
    consumer.close()
