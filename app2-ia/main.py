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
from ultralytics import YOLO
from ultralytics.utils.downloads import attempt_download_asset

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import KAFKA_BROKER, TOPIC_IMAGE_TILES, TOPIC_STATUS, TOPIC_TILE_DETECTIONS, TOPIC_CONTROL
from shared import storage

# --- CONFIGURATION KAFKA ---
TOPIC_IN = TOPIC_IMAGE_TILES
TOPIC_OUT = TOPIC_TILE_DETECTIONS

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app2-ia")

YOLO_MODEL_ASSETS_DIR = Path(os.getenv("AERIAL_MODEL_DIR", "/opt/modelzoo"))
YOLO_MODEL_IMAGE_SIZE = int(os.getenv("AERIAL_MODEL_IMGSZ", "1024"))
YOLO_MODEL_RELEASE = os.getenv("AERIAL_MODEL_RELEASE", "v8.4.0")

YOLO_MODEL_VARIANTS = {
    "yolo26l": {"checkpoint": "yolo26l-obb.pt"},
    "yolo26m": {"checkpoint": "yolo26m-obb.pt"},
    "yolo26s": {"checkpoint": "yolo26s-obb.pt"},
    "yolo26n": {"checkpoint": "yolo26n-obb.pt"},
    "yolo11l": {"checkpoint": "yolo11l-obb.pt"},
    "yolo11m": {"checkpoint": "yolo11m-obb.pt"},
    "yolo11s": {"checkpoint": "yolo11s-obb.pt"},
    "yolo11n": {"checkpoint": "yolo11n-obb.pt"},
}

YOLO_MODEL_ALIASES = {
    "best": "yolo26l",
    "l": "yolo26l",
    "m": "yolo26m",
    "s": "yolo26s",
    "n": "yolo26n",
    "tiny": "yolo26n",
    "26l": "yolo26l",
    "26m": "yolo26m",
    "26s": "yolo26s",
    "26n": "yolo26n",
    "11l": "yolo11l",
    "11m": "yolo11m",
    "11s": "yolo11s",
    "11n": "yolo11n",
}

SAM3_MODEL_ID = os.getenv("SAM3_MODEL_ID", "facebook/sam3")
SAM3_DEFAULT_PROMPT = os.getenv("SAM3_DEFAULT_PROMPT", "car")
SAM3_MASK_THRESHOLD = float(os.getenv("SAM3_MASK_THRESHOLD", "0.5"))

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

device_type = "cuda" if torch.cuda.is_available() else "cpu"
yolo_device = 0 if device_type == "cuda" else "cpu"
sam3_autocast_dtype = torch.bfloat16 if device_type == "cuda" else torch.float32

_yolo_models: dict[str, tuple[YOLO, list[str]]] = {}
_sam3_model = None
_sam3_processor = None


def normalize_yolo_model_variant(value: str | None) -> str:
    normalized = str(value or os.getenv("AERIAL_MODEL_VARIANT", "best")).strip().lower().replace("_", "").replace("-", "")
    if normalized in YOLO_MODEL_VARIANTS:
        return normalized
    return YOLO_MODEL_ALIASES.get(normalized, "yolo26l")


def resolve_yolo_model_file(requested_variant: str | None = None) -> tuple[str, Path, str]:
    variant_name = normalize_yolo_model_variant(requested_variant)
    variant = YOLO_MODEL_VARIANTS[variant_name]
    configured_model = os.getenv("AERIAL_MODEL_FILE", "").strip()
    checkpoint_name = variant["checkpoint"]
    if configured_model:
        model_path = Path(configured_model)
        if model_path.name:
            checkpoint_name = model_path.name
    else:
        model_path = YOLO_MODEL_ASSETS_DIR / checkpoint_name
    return variant_name, model_path, checkpoint_name


def ensure_yolo_model_file(model_path: Path, checkpoint_name: str) -> Path:
    if model_path.exists():
        return model_path

    model_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading aerial detector checkpoint %s to %s", checkpoint_name, model_path)
    downloaded_path = Path(attempt_download_asset(checkpoint_name, repo="ultralytics/assets", release=YOLO_MODEL_RELEASE))
    if downloaded_path.resolve() != model_path.resolve():
        shutil.copy2(downloaded_path, model_path)
    return model_path


def load_yolo_model(requested_variant: str | None = None) -> tuple[YOLO, list[str], str]:
    selected_variant, model_file_path, model_file_name = resolve_yolo_model_file(requested_variant)
    cache_key = str(model_file_path.resolve())
    cached_model = _yolo_models.get(cache_key)
    if cached_model is not None:
        model, available_labels = cached_model
        return model, available_labels, selected_variant

    model_file_path = ensure_yolo_model_file(model_file_path, model_file_name)
    logger.info(
        "Loading YOLO aerial detector variant=%s checkpoint=%s device=%s imgsz=%s",
        selected_variant,
        model_file_path,
        yolo_device,
        YOLO_MODEL_IMAGE_SIZE,
    )
    model = YOLO(str(model_file_path))
    available_labels = list((model.names or {}).values())
    if not available_labels:
        raise RuntimeError(f"YOLO model did not expose class names: {model_file_path}")
    _yolo_models[cache_key] = (model, available_labels)
    return model, available_labels, selected_variant


def load_sam3_model() -> tuple[Sam3Model, Sam3Processor]:
    global _sam3_model, _sam3_processor
    if _sam3_model is not None and _sam3_processor is not None:
        return _sam3_model, _sam3_processor

    logger.info("Loading SAM3 model=%s device=%s", SAM3_MODEL_ID, device_type)
    _sam3_model = Sam3Model.from_pretrained(SAM3_MODEL_ID).to(device_type)
    _sam3_processor = Sam3Processor.from_pretrained(SAM3_MODEL_ID)
    return _sam3_model, _sam3_processor


def to_numpy(value):
    if value is None:
        return None
    if hasattr(value, "tensor"):
        value = value.tensor
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    return np.asarray(value)


def polygon_center(polygon: list[list[float]]) -> tuple[float, float]:
    if not polygon:
        return 0.0, 0.0
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))


def resolve_requested_labels(requested_classes: list[str], available_labels: list[str]) -> list[str]:
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


def extract_obb_detections(raw_result, requested_labels: list[str], min_confidence: float) -> list[dict]:
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


def run_yolo_detection(tile_path: str, requested_classes: list[str], requested_conf: float, requested_model_variant: str | None = None) -> tuple[list[dict], dict]:
    model, available_labels, selected_variant = load_yolo_model(requested_model_variant)
    requested_labels = resolve_requested_labels(requested_classes, available_labels)
    fallback_conf = max(0.10, min(requested_conf, 0.20))
    raw_results = model.predict(
        source=tile_path,
        conf=fallback_conf,
        imgsz=YOLO_MODEL_IMAGE_SIZE,
        device=yolo_device,
        verbose=False,
    )
    raw_result = raw_results[0] if raw_results else None
    attempts = [
        {"conf": requested_conf, "label": f"YOLO primary pass conf={requested_conf:.2f}"},
        {"conf": fallback_conf, "label": "YOLO fallback pass with lower threshold"},
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
    best_attempt = {**best_attempt, "label": f"{best_attempt['label']} model={selected_variant}"}
    return best_detections, best_attempt


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
