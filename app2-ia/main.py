import os
import json
import logging
import sys
import shutil
from typing import Any

import numpy as np
import torch
from pyproj import Transformer
from confluent_kafka import Consumer, Producer
from pathlib import Path
from ultralytics import YOLO
from ultralytics.utils.downloads import attempt_download_asset

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_IMAGE_TILES, TOPIC_STATUS, TOPIC_TILE_DETECTIONS

# --- CONFIGURATION KAFKA ---
TOPIC_IN = TOPIC_IMAGE_TILES
TOPIC_OUT = TOPIC_TILE_DETECTIONS

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app2-ia")

MODEL_ASSETS_DIR = Path(os.getenv("AERIAL_MODEL_DIR", "/opt/modelzoo"))
MODEL_IMAGE_SIZE = int(os.getenv("AERIAL_MODEL_IMGSZ", "1024"))
MODEL_RELEASE = os.getenv("AERIAL_MODEL_RELEASE", "v8.4.0")

MODEL_VARIANTS = {
    "best": {
        "checkpoint": "yolo26s-obb.pt",
    },
    "l": {
        "checkpoint": "yolo26l-obb.pt",
    },
    "m": {
        "checkpoint": "yolo26m-obb.pt",
    },
    "s": {
        "checkpoint": "yolo26s-obb.pt",
    },
    "tiny": {
        "checkpoint": "yolo26n-obb.pt",
    },
}

REQUESTED_CLASS_MAP = {
    "car": {"small-vehicle", "large-vehicle"},
    "truck": {"large-vehicle"},
    "bus": {"large-vehicle"},
    "motorcycle": {"small-vehicle"},
    "bicycle": {"small-vehicle"},
    "airplane": {"plane"},
    "boat": {"ship"},
}

DEFAULT_AERIAL_CLASSES = {"small-vehicle", "large-vehicle"}


def resolve_model_file():
    variant_name = os.getenv("AERIAL_MODEL_VARIANT", "best").strip().lower() or "best"
    variant = MODEL_VARIANTS.get(variant_name, MODEL_VARIANTS["best"])
    configured_model = os.getenv("AERIAL_MODEL_FILE", "").strip()
    checkpoint_name = variant["checkpoint"]
    if configured_model:
        model_path = Path(configured_model)
        if model_path.name:
            checkpoint_name = model_path.name
    else:
        model_path = MODEL_ASSETS_DIR / checkpoint_name
    return variant_name, model_path, checkpoint_name


def ensure_model_file(model_path: Path, checkpoint_name: str):
    if model_path.exists():
        return model_path

    model_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading aerial detector checkpoint %s to %s", checkpoint_name, model_path)
    downloaded_path = Path(attempt_download_asset(checkpoint_name, repo="ultralytics/assets", release=MODEL_RELEASE))
    if downloaded_path.resolve() != model_path.resolve():
        shutil.copy2(downloaded_path, model_path)
    return model_path


def to_numpy(value: Any):
    if value is None:
        return None
    if hasattr(value, "tensor"):
        value = value.tensor
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def polygon_center(polygon):
    if not polygon:
        return 0.0, 0.0
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def resolve_requested_labels(requested_classes, available_labels):
    resolved = set()
    unsupported = []
    for requested in requested_classes or []:
        mapped = REQUESTED_CLASS_MAP.get(str(requested).strip().lower())
        if mapped:
            resolved.update(mapped)
        else:
            unsupported.append(requested)

    if unsupported:
        logger.info(
            "Requested classes %s are not supported by the aerial vehicle detector; using aerial vehicle labels instead.",
            unsupported,
        )

    if not resolved:
        resolved = set(DEFAULT_AERIAL_CLASSES)

    filtered = [label for label in available_labels if label in resolved]
    return filtered or [label for label in available_labels if label in DEFAULT_AERIAL_CLASSES]


def extract_obb_detections(raw_result, requested_labels, min_confidence):
    detections = []
    requested = set(requested_labels)
    if raw_result is None:
        return detections

    oriented_boxes = getattr(raw_result, "obb", None)
    if oriented_boxes is None:
        return detections

    polygons = to_numpy(getattr(oriented_boxes, "xyxyxyxy", None))
    labels = to_numpy(getattr(oriented_boxes, "cls", None))
    scores = to_numpy(getattr(oriented_boxes, "conf", None))
    names = raw_result.names or {}
    if polygons is None or labels is None or scores is None:
        return detections

    for polygon, label_index, score in zip(polygons, labels, scores):
        class_id = int(label_index)
        class_name = names.get(class_id, str(class_id))
        if class_name not in requested or float(score) < min_confidence:
            continue
        polygon_points = [[float(x), float(y)] for x, y in np.asarray(polygon).reshape(-1, 2)]
        center_x, center_y = polygon_center(polygon_points)
        detections.append({
            "polygon": polygon_points,
            "center_x": center_x,
            "center_y": center_y,
            "confidence": float(score),
            "class_id": class_id,
            "class_name": class_name,
        })

    return detections

consumer = Consumer({
    'bootstrap.servers': KAFKA_BROKER,
    'group.id': 'ia-tile-workers',
    'auto.offset.reset': 'earliest'
})
consumer.subscribe([TOPIC_IN])

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
mission_stats = {}


def resolve_host_path(path_value):
    if path_value.startswith("/host"):
        return path_value
    if not path_value.startswith("/"):
        path_value = "/" + path_value
    return "/host" + path_value


def transform_detection_coordinates(ortho_transform, transformer, gx, gy):
    if not ortho_transform or transformer is None:
        return None, None
    c, a, b, f, d, e = ortho_transform
    proj_x = c + a * gx + b * gy
    proj_y = f + d * gx + e * gy
    lon, lat = transformer.transform(proj_x, proj_y)
    return float(lon), float(lat)


def translate_segment(segment, offset_x, offset_y):
    return [
        [float(point[0] + offset_x), float(point[1] + offset_y)]
        for point in segment
    ]

def report_progress(vol_id, step, progress, status="processing", log=None):
    msg = {"vol_id": vol_id, "step": step, "progress": progress, "status": status, "service": "IA"}
    if log:
        msg["log"] = log
        print(f"[{step}] {log}")
    producer.produce(TOPIC_STATUS, key=vol_id, value=json.dumps(msg))
    producer.flush()

selected_variant, model_file_path, model_file_name = resolve_model_file()
model_file_path = ensure_model_file(model_file_path, model_file_name)

device_type = 0 if torch.cuda.is_available() else 'cpu'
logger.info("Loading aerial detector variant=%s checkpoint=%s device=%s imgsz=%s", selected_variant, model_file_path, device_type, MODEL_IMAGE_SIZE)
model = YOLO(str(model_file_path))
available_model_labels = list((model.names or {}).values())
if not available_model_labels:
    raise RuntimeError(f"YOLO26 model did not expose class names: {model_file_path}")


def run_detection(tile_path, requested_labels, requested_conf):
    fallback_conf = max(0.10, min(requested_conf, 0.20))
    raw_results = model.predict(
        source=tile_path,
        conf=fallback_conf,
        imgsz=MODEL_IMAGE_SIZE,
        device=device_type,
        verbose=False,
    )
    raw_result = raw_results[0] if raw_results else None
    attempts = [
        {"conf": requested_conf, "label": f"primary pass conf={requested_conf:.2f}"},
        {"conf": fallback_conf, "label": "fallback pass with lower threshold"},
    ]

    best_detections = []
    best_attempt = attempts[0]
    for attempt in attempts:
        detections = extract_obb_detections(raw_result, requested_labels, attempt["conf"])
        if len(detections) > len(best_detections):
            best_detections = detections
            best_attempt = attempt
        if detections:
            break
    return best_detections, best_attempt

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
        
        requested_labels = resolve_requested_labels(req_classes, available_model_labels)
        tile_path = resolve_host_path(tile_path)
            
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

        detections_for_tile, attempt = run_detection(tile_path, requested_labels, req_conf)
        
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

except KeyboardInterrupt:
    print("Arrêt demandé par l'utilisateur.")
finally:
    consumer.close()
