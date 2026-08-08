import logging
import os
import shutil
import sys
import threading
from pathlib import Path

import cv2
import numpy as np
import torch
from confluent_kafka import Consumer, Producer
from huggingface_hub import hf_hub_download
from PIL import Image
from pyproj import Transformer
from transformers import Sam3Model, Sam3Processor

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_IMAGE_TILES,
    TOPIC_STATUS,
    TOPIC_TILE_DETECTIONS,
)
from shared.cancellation import AttemptCancellationRegistry
from shared.event_contracts import deterministic_event_id, make_event
from shared.kafka_reliability import (
    process_message,
    publish_json,
    reliable_consumer_config,
)
from shared.model_provenance import (
    build_model_manifest,
    immutable_revision,
    installed_versions,
    sha256_file,
)
from shared.pipeline_params import normalize_ai_backend as normalize_backend_name
from shared.worker_messaging import (
    make_cancellation_handler,
    make_progress_publisher,
    run_control_consumer,
)
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
SAM3_MODEL_REVISION = immutable_revision(os.getenv("SAM3_MODEL_REVISION", "3c879f39826c281e95690f02c7821c4de09afae7"))
SAM3_DEFAULT_PROMPT = os.getenv("SAM3_DEFAULT_PROMPT", "car")
SAM3_MASK_THRESHOLD = float(os.getenv("SAM3_MASK_THRESHOLD", "0.5"))

device_type = "cuda" if torch.cuda.is_available() else "cpu"
sam3_autocast_dtype = torch.bfloat16 if device_type == "cuda" else torch.float32

_sam3_model = None
_sam3_processor = None
_sam3_artifact_sha256: str | None = None


def load_sam3_model() -> tuple[Sam3Model, Sam3Processor]:
    global _sam3_artifact_sha256, _sam3_model, _sam3_processor
    if _sam3_model is not None and _sam3_processor is not None:
        return _sam3_model, _sam3_processor

    logger.info(
        "Loading SAM3 model=%s revision=%s device=%s",
        SAM3_MODEL_ID,
        SAM3_MODEL_REVISION,
        device_type,
    )
    artifact_path = hf_hub_download(
        repo_id=SAM3_MODEL_ID,
        filename="model.safetensors",
        revision=SAM3_MODEL_REVISION,
    )
    _sam3_artifact_sha256 = sha256_file(artifact_path)
    _sam3_model = Sam3Model.from_pretrained(
        SAM3_MODEL_ID,
        revision=SAM3_MODEL_REVISION,
    ).to(device_type)
    _sam3_processor = Sam3Processor.from_pretrained(
        SAM3_MODEL_ID,
        revision=SAM3_MODEL_REVISION,
    )
    return _sam3_model, _sam3_processor


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
    assert _sam3_artifact_sha256 is not None
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
    attempt = {
        "label": f"SAM3 prompt='{prompt}' conf={requested_conf:.2f}",
        "model_manifest": build_model_manifest(
            backend="sam3",
            repository=SAM3_MODEL_ID,
            revision=SAM3_MODEL_REVISION,
            artifact="model.safetensors",
            artifact_sha256=_sam3_artifact_sha256,
            libraries=installed_versions("transformers", "torch"),
            runtime={
                "device": device_type,
                "autocast_dtype": str(sam3_autocast_dtype),
            },
            inference={
                "prompt": prompt,
                "confidence": requested_conf,
                "mask_threshold": SAM3_MASK_THRESHOLD,
            },
        ),
    }
    if masks is None or boxes is None or scores is None or len(scores) == 0:
        return [], attempt

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

    return detections, attempt


CONSUMER_GROUP = "ia-tile-workers"


def create_work_consumer():
    work_consumer = Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            CONSUMER_GROUP,
            offset_reset="earliest",
        )
    )
    work_consumer.subscribe([TOPIC_IN])
    return work_consumer


producer = Producer({"bootstrap.servers": KAFKA_BROKER})
progress_publisher = make_progress_publisher(
    producer,
    TOPIC_STATUS,
    service_name="IA",
)
mission_stats = {}


cancel_manager = AttemptCancellationRegistry()


def control_consumer_thread():
    run_control_consumer(
        kafka_broker=KAFKA_BROKER,
        topic=TOPIC_CONTROL,
        consumer_group="ia-control-workers",
        producer=producer,
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=make_cancellation_handler(cancel_manager, logger),
        logger=logger,
    )


def transform_detection_coordinates(
    ortho_transform, transformer, gx: float, gy: float
) -> tuple[float | None, float | None]:
    if not ortho_transform or transformer is None:
        return None, None
    c, a, b, f, d, e = ortho_transform
    proj_x = c + a * gx + b * gy
    proj_y = f + d * gx + e * gy
    lon, lat = transformer.transform(proj_x, proj_y)
    return float(lon), float(lat)


def translate_segment(segment: list[list[float]], offset_x: float, offset_y: float) -> list[list[float]]:
    return [[float(point[0] + offset_x), float(point[1] + offset_y)] for point in segment]


def report_progress(vol_id: str, step: str, progress: int, status: str = "processing", log: str | None = None) -> None:
    if log:
        print(f"[{step}] {log}")
    progress_publisher(
        vol_id,
        step,
        progress,
        status=status,
        log=log,
    )


def run_detection(tile_path: str, tile_info: dict) -> tuple[list[dict], dict]:
    backend = normalize_backend_name(tile_info.get("ai_backend"))
    requested_conf = float(tile_info.get("ai_confidence", 0.3))
    requested_classes = tile_info.get("classes", ["car"])
    if backend == "sam3":
        return run_sam3_detection(tile_path, resolve_sam3_prompt(tile_info), requested_conf)
    return run_yolo_detection(tile_path, requested_classes, requested_conf, tile_info.get("ai_model_variant"))


def process_tile(tile_info):
    vol_id = tile_info["vol_id"]
    analysis_run_id = tile_info.get("analysis_run_id")
    analysis_attempt = int(tile_info.get("attempt", 0))
    stats_key = (analysis_run_id or vol_id, analysis_attempt)
    total_tiles = int(tile_info.get("total_tiles", 0) or 0)

    tile_s3_key = tile_info.get("tile_s3_key") or tile_info.get("tile_path", "")
    local_tile_dir = f"/tmp/ia_tiles/{vol_id}/{analysis_run_id or 'pipeline'}"
    os.makedirs(local_tile_dir, exist_ok=True)
    tile_filename = tile_s3_key.split("/")[-1] if "/" in tile_s3_key else tile_s3_key
    tile_path = os.path.join(local_tile_dir, tile_filename)

    offset_x = tile_info["offset_x"]
    offset_y = tile_info["offset_y"]

    if cancel_manager.is_cancelled(
        vol_id,
        analysis_run_id,
        analysis_attempt,
    ):
        if os.path.isdir(local_tile_dir):
            shutil.rmtree(local_tile_dir, ignore_errors=True)
        mission_stats.pop(stats_key, None)
        return

    ortho_transform = tile_info.get("ortho_transform")
    ortho_crs = tile_info.get("ortho_crs")

    try:
        storage.download_file(tile_s3_key, tile_path)
    except Exception as dl_err:
        report_progress(
            vol_id, "ERROR", 0, status="error", log=f"Failed to download tile from S3: {tile_s3_key} — {dl_err}"
        )
        raise

    stats = mission_stats.setdefault(
        stats_key,
        {"processed": 0, "detections": 0, "total_tiles": total_tiles},
    )
    if total_tiles:
        stats["total_tiles"] = total_tiles

    detections_for_tile, attempt = run_detection(tile_path, tile_info)
    detections = []

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
                logger.debug("Failed to geolocate detection for %s tile %s: %s", vol_id, tile_info["tile_index"], error)

        global_segment = translate_segment(detection["polygon"], offset_x, offset_y)
        detections.append(
            {
                "vol_id": vol_id,
                "global_pixel_x": float(gx),
                "global_pixel_y": float(gy),
                "geo_lon": geo_lon,
                "geo_lat": geo_lat,
                "confidence": round(float(detection["confidence"]), 2),
                "class_id": int(detection["class_id"]),
                "class_name": detection["class_name"],
                "segment": global_segment,
            }
        )

    tile_result = make_event(
        "tile_detection",
        {
            "vol_id": vol_id,
            "tile_index": tile_info["tile_index"],
            "detections": detections,
            "analysis_run_id": analysis_run_id,
            "model_manifest": attempt["model_manifest"],
        },
        event_id=deterministic_event_id(
            "tile_detection",
            vol_id,
            analysis_run_id or "pipeline",
            tile_info["tile_index"],
            analysis_attempt,
        ),
        correlation_id=tile_info.get("correlation_id"),
        causation_id=tile_info.get("event_id"),
        attempt=analysis_attempt,
    )
    publish_json(producer, TOPIC_OUT, tile_result, key=str(vol_id))

    stats["processed"] += 1
    stats["detections"] += len(detections)
    total = stats.get("total_tiles") or stats["processed"]
    progress = min(99, int((stats["processed"] / max(total, 1)) * 100))
    if detections:
        report_progress(
            vol_id,
            "DETECTING",
            progress,
            log=f"Tile {tile_info['tile_index']} produced {len(detections)} detections via {attempt['label']}",
        )
    elif stats["processed"] == 1 or stats["processed"] % 10 == 0:
        report_progress(
            vol_id,
            "DETECTING",
            progress,
            log=f"Processed {stats['processed']}/{total} tiles, detections={stats['detections']} ({attempt['label']})",
        )

    if total_tiles and stats["processed"] >= total_tiles:
        summary = f"IA finished {stats['processed']} tiles with {stats['detections']} detections"
        report_progress(vol_id, "DETECTING", 100, status="success", log=summary)
        mission_stats.pop(stats_key, None)
        cancel_manager.clear(vol_id, analysis_run_id, analysis_attempt)
        if os.path.isdir(local_tile_dir):
            shutil.rmtree(local_tile_dir, ignore_errors=True)
        import gc

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def worker_main():
    work_consumer = create_work_consumer()
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    print("App 2 (IA Workers) waiting for tiles on Kafka...")
    try:
        while True:
            message = work_consumer.poll(1.0)
            if message is None or message.error():
                continue
            process_message(
                consumer=work_consumer,
                producer=producer,
                message=message,
                consumer_group=CONSUMER_GROUP,
                expected_type="image_tile",
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=process_tile,
                logger=logger,
            )
    except KeyboardInterrupt:
        print("Shutdown requested by user.")
    finally:
        work_consumer.close()


if __name__ == "__main__":
    worker_main()
