import json
import logging
import os
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import threading
import torch
from confluent_kafka import Consumer, Producer
from PIL import Image
from pyproj import Transformer
from transformers import Sam3Model, Sam3Processor

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_IMAGE_TILES, TOPIC_STATUS, TOPIC_TILE_DETECTIONS, TOPIC_CONTROL
from shared import storage
from detection_core import polygon_center, run_yolo_detection

# --- CONFIGURATION KAFKA ---
TOPIC_IN = TOPIC_IMAGE_TILES
TOPIC_OUT = TOPIC_TILE_DETECTIONS

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app2-ia")

SAM3_MODEL_ID = os.getenv("SAM3_MODEL_ID", "facebook/sam3")
SAM3_DEFAULT_PROMPT = os.getenv("SAM3_DEFAULT_PROMPT", "car")
SAM3_MASK_THRESHOLD = float(os.getenv("SAM3_MASK_THRESHOLD", "0.5"))

device_type = "cuda" if torch.cuda.is_available() else "cpu"
sam3_autocast_dtype = torch.bfloat16 if device_type == "cuda" else torch.float32

_sam3_model = None
_sam3_processor = None


def load_sam3_model() -> tuple[Sam3Model, Sam3Processor]:
    global _sam3_model, _sam3_processor
    if _sam3_model is not None and _sam3_processor is not None:
        return _sam3_model, _sam3_processor

    logger.info("Loading SAM3 model=%s device=%s", SAM3_MODEL_ID, device_type)
    _sam3_model = Sam3Model.from_pretrained(SAM3_MODEL_ID).to(device_type)
    _sam3_processor = Sam3Processor.from_pretrained(SAM3_MODEL_ID)
    return _sam3_model, _sam3_processor


def normalize_backend_name(value: str | None) -> str:
    normalized = str(value or "yolo").strip().lower().replace("_", "-").replace(" ", "-")
    if normalized in {"sam", "sam3", "sam-3", "meta-sam3", "meta-sam-3", "segment-anything-3"}:
        return "sam3"
    return "yolo"


def resolve_sam3_prompt(tile_info: dict) -> str:
    explicit_prompt = str(tile_info.get("sam_prompt") or "").strip()
    if explicit_prompt:
        return explicit_prompt

    requested_classes = tile_info.get("classes") or []
    if requested_classes:
        return str(requested_classes[0]).strip()
    return SAM3_DEFAULT_PROMPT


def contour_to_polygon(mask: np.ndarray, fallback_box: list[list[float]]) -> tuple[list[list[float]], float, float]:
    binary_mask = (mask > 0).astype(np.uint8)
    contours, _ = cv2.findContours(binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        center_x, center_y = polygon_center(fallback_box)
        return fallback_box, center_x, center_y

    contour = max(contours, key=cv2.contourArea)
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(1.0, 0.01 * perimeter)
    simplified = cv2.approxPolyDP(contour, epsilon, True)
    polygon = [[float(point[0][0]), float(point[0][1])] for point in simplified]
    if len(polygon) < 3:
        polygon = fallback_box

    moments = cv2.moments(contour)
    if moments["m00"]:
        center_x = float(moments["m10"] / moments["m00"])
        center_y = float(moments["m01"] / moments["m00"])
    else:
        center_x, center_y = polygon_center(polygon)
    return polygon, center_x, center_y


def run_sam3_detection(tile_path: str, prompt: str, requested_conf: float) -> tuple[list[dict], dict]:
    model, processor = load_sam3_model()
    image = Image.open(tile_path).convert("RGB")
    inputs = processor(images=image, text=prompt, return_tensors="pt").to(device_type)

    with torch.no_grad():
        with torch.autocast(device_type="cuda", dtype=sam3_autocast_dtype, enabled=device_type == "cuda"):
            outputs = model(**inputs)

    result = processor.post_process_instance_segmentation(
        outputs,
        threshold=requested_conf,
        mask_threshold=SAM3_MASK_THRESHOLD,
        target_sizes=inputs.get("original_sizes").tolist(),
    )[0]

    masks = result.get("masks")
    boxes = result.get("boxes")
    scores = result.get("scores")
    if masks is None or boxes is None or scores is None or len(scores) == 0:
        return [], {"label": f"SAM3 prompt='{prompt}' conf={requested_conf:.2f}"}

    detections = []
    for mask, box, score in zip(masks, boxes, scores):
        x1, y1, x2, y2 = [float(value) for value in box.tolist()]
        fallback_box = [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
        polygon, center_x, center_y = contour_to_polygon(mask.detach().cpu().numpy(), fallback_box)
        detections.append(
            {
                "polygon": polygon,
                "center_x": center_x,
                "center_y": center_y,
                "confidence": float(score),
                "class_id": 0,
                "class_name": prompt,
            }
        )

    return detections, {"label": f"SAM3 prompt='{prompt}' conf={requested_conf:.2f}"}

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'ia-tile-workers',
    'auto.offset.reset': 'earliest'
})
consumer.subscribe([TOPIC_IN])

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
mission_stats = {}


class CancelManager:
    def __init__(self):
        self._set = set()
        self._lock = threading.Lock()
    def cancel(self, v):
        with self._lock: self._set.add(v)
    def is_cancelled(self, v):
        with self._lock: return v in self._set

cancel_manager = CancelManager()

def control_consumer_thread():
    control_consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'ia-control-workers',
        'auto.offset.reset': 'latest'
    })
    control_consumer.subscribe([TOPIC_CONTROL])
    while True:
        msg = control_consumer.poll(1.0)
        if msg is None or msg.error(): continue
        try:
            data = json.loads(msg.value().decode('utf-8'))
            if data.get("command") == "cancel":
                vid = data.get("vol_id")
                if vid:
                    cancel_manager.cancel(vid)
                    logger.info("⚠️ Cancel requested for %s", vid)
        except Exception:
            pass

threading.Thread(target=control_consumer_thread, daemon=True).start()

def transform_detection_coordinates(ortho_transform, transformer, gx: float, gy: float) -> tuple[float | None, float | None]:
    if not ortho_transform or transformer is None:
        return None, None
    c, a, b, f, d, e = ortho_transform
    proj_x = c + a * gx + b * gy
    proj_y = f + d * gx + e * gy
    lon, lat = transformer.transform(proj_x, proj_y)
    return float(lon), float(lat)


def translate_segment(segment: list[list[float]], offset_x: float, offset_y: float) -> list[list[float]]:
    return [
        [float(point[0] + offset_x), float(point[1] + offset_y)]
        for point in segment
    ]

def report_progress(vol_id: str, step: str, progress: int, status: str = "processing", log: str | None = None) -> None:
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "IA"}
    if log:
        msg["log"] = log
        print(f"[{step}] {log}")
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()

def run_detection(tile_path: str, tile_info: dict) -> tuple[list[dict], dict]:
    backend = normalize_backend_name(tile_info.get("ai_backend"))
    requested_conf = float(tile_info.get("ai_confidence", 0.3))
    requested_classes = tile_info.get("classes", ["car"])
    if backend == "sam3":
        return run_sam3_detection(tile_path, resolve_sam3_prompt(tile_info), requested_conf)
    return run_yolo_detection(tile_path, requested_classes, requested_conf, tile_info.get("ai_model_variant"))

print("App 2 (IA Workers) waiting for tiles on Kafka...")

try:
    while True:
        msg = consumer.poll(1.0)
        if msg is None: continue
        if msg.error(): continue
            
        # 1. Lecture de la tuile
        tile_info = json.loads(msg.value().decode('utf-8'))
        vol_id = tile_info['vol_id']
        total_tiles = int(tile_info.get('total_tiles', 0) or 0)

        # Download tile from S3 to a local temp path
        tile_s3_key = tile_info.get('tile_s3_key') or tile_info.get('tile_path', '')
        local_tile_dir = f"/tmp/ia_tiles/{vol_id}"
        os.makedirs(local_tile_dir, exist_ok=True)
        tile_filename = tile_s3_key.split('/')[-1] if '/' in tile_s3_key else tile_s3_key
        tile_path = os.path.join(local_tile_dir, tile_filename)

        offset_x = tile_info['offset_x']
        offset_y = tile_info['offset_y']

        if cancel_manager.is_cancelled(vol_id):
            # Clean up any downloaded tiles for this cancelled mission
            if os.path.isdir(local_tile_dir):
                shutil.rmtree(local_tile_dir, ignore_errors=True)
            mission_stats.pop(vol_id, None)
            continue

        # We assume the orthomosaic transform and CRS are passed in the message to compute real-world coordinates
        ortho_transform = tile_info.get('ortho_transform')
        ortho_crs = tile_info.get('ortho_crs')

        try:
            storage.download_file(tile_s3_key, tile_path)
        except Exception as dl_err:
            report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to download tile from S3: {tile_s3_key} — {dl_err}")
            continue

        stats = mission_stats.setdefault(vol_id, {"processed": 0, "detections": 0, "total_tiles": total_tiles})
        if total_tiles:
            stats["total_tiles"] = total_tiles

        detections_for_tile, attempt = run_detection(tile_path, tile_info)
        
        detections = []
        
        # Geolocation transformer setup if CRS is available
        proj_transformer = None
        if ortho_crs and ortho_crs != "unknown":
            try:
                proj_transformer = Transformer.from_crs(ortho_crs, "EPSG:4326", always_xy=True)
            except Exception as e:
                logger.warning("Failed to create CRS transformer for %s: %s", vol_id, e)

        for detection in detections_for_tile:
            gx = detection["center_x"] + offset_x
            gy = detection["center_y"] + offset_y

            geo_lat = None
            geo_lon = None
            if ortho_transform and proj_transformer:
                try:
                    geo_lon, geo_lat = transform_detection_coordinates(ortho_transform, proj_transformer, gx, gy)
                except Exception as error:
                    logger.debug("Failed to geolocate detection for %s tile %s: %s", vol_id, tile_info['tile_index'], error)

            global_segment = translate_segment(detection["polygon"], offset_x, offset_y)

            detections.append({
                "vol_id": vol_id,
                "global_pixel_x": float(gx),
                "global_pixel_y": float(gy),
                "geo_lon": geo_lon,
                "geo_lat": geo_lat,
                "confidence": round(float(detection["confidence"]), 2),
                "class_id": int(detection["class_id"]),
                "class_name": detection["class_name"],
                "segment": global_segment,
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
            # Clean up local tile files for this mission
            tile_cleanup_dir = f"/tmp/ia_tiles/{vol_id}"
            if os.path.isdir(tile_cleanup_dir):
                shutil.rmtree(tile_cleanup_dir, ignore_errors=True)
            # Free VRAM after each completed mission
            import gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

except KeyboardInterrupt:
    print("Shutdown requested by user.")
finally:
    consumer.close()
