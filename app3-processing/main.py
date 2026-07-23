import os
import json
import logging
import shutil
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

from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_IMAGE_TILES,
    TOPIC_ORTHO,
    TOPIC_STATUS,
    TOPIC_TILE_DETECTIONS,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.kafka_reliability import (
    process_message,
    reliable_consumer_config,
)
from shared.pipeline_params import normalize_ai_backend
from shared.worker_messaging import (
    make_progress_publisher,
    run_control_consumer,
)
from shared import storage
from shared.database import (
    get_session,
    get_or_create_mission,
    Mission,
    Detection as DBDetection,
    count_received_tiles,
    get_mission_detections,
)
from processing_core import (
    build_tile_starts,
    dedupe_mission_detections as dedupe_detection_core,
)

# --- CONFIGURATION KAFKA ---
TOPIC_IN_ORTHO = TOPIC_ORTHO
TOPIC_OUT_TILES = TOPIC_IMAGE_TILES
TOPIC_IN_DETECTIONS = TOPIC_TILE_DETECTIONS

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app3-processing")

CONSUMER_GROUP = "processing-group"


def create_work_consumer():
    work_consumer = Consumer(
        reliable_consumer_config(
            KAFKA_BROKER,
            CONSUMER_GROUP,
            offset_reset="earliest",
            **{"max.poll.interval.ms": 7200000},
        )
    )
    work_consumer.subscribe([TOPIC_IN_ORTHO, TOPIC_IN_DETECTIONS])
    return work_consumer

producer = Producer({'bootstrap.servers': KAFKA_BROKER})
progress_publisher = make_progress_publisher(
    producer,
    TOPIC_STATUS,
    service_name="TILER",
)

class CancelManager:
    def __init__(self):
        self._cancelled_vols = set()
        self._lock = threading.Lock()

    def cancel(self, vol_id):
        with self._lock:
            self._cancelled_vols.add(vol_id)

    def is_cancelled(self, vol_id):
        with self._lock:
            return vol_id in self._cancelled_vols

    def clear(self, vol_id):
        with self._lock:
            self._cancelled_vols.discard(vol_id)

cancel_manager = CancelManager()

def control_consumer_thread():
    def handle_control(data):
        if data.get("command") != "cancel":
            return
        vol_id = data.get("vol_id")
        if vol_id:
            cancel_manager.cancel(vol_id)
            logger.info("⚠️ Cancel requested for %s", vol_id)

    run_control_consumer(
        kafka_broker=KAFKA_BROKER,
        topic=TOPIC_CONTROL,
        consumer_group="processing-control-workers",
        producer=producer,
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=handle_control,
        logger=logger,
    )

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
            if tile_index in mission['received_tiles']:
                total = mission.get('total_tiles')
                return mission, total is not None and len(mission['received_tiles']) == total
            mission['detections'].extend(detections)
            mission['received_tiles'].add(tile_index)
            total = mission.get('total_tiles')
            is_complete = total is not None and len(mission['received_tiles']) == total
            return mission, is_complete

    def pop(self, vol_id):
        with self._lock:
            return self._missions.pop(vol_id, None)


missions = MissionRegistry()


def cleanup_tiles_directory(tiles_dir):
    for entry in os.listdir(tiles_dir):
        if entry.startswith("tile_") and entry.lower().endswith(".jpg"):
            try:
                os.remove(os.path.join(tiles_dir, entry))
            except OSError as error:
                logger.warning("Failed to remove stale tile %s: %s", entry, error)


def report_progress(vol_id, step, progress, status="processing", log=None):
    progress_publisher(
        vol_id,
        step,
        progress,
        status=status,
        log=log,
    )


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


def dedupe_mission_detections(detections):
    center_threshold = float(os.getenv("UNTILER_DEDUPE_CENTER_THRESHOLD", "40"))
    iou_threshold = float(os.getenv("UNTILER_DEDUPE_IOU_THRESHOLD", "0.05"))
    return dedupe_detection_core(
        detections,
        center_threshold=center_threshold,
        iou_threshold=iou_threshold,
    )


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
    ortho_s3_key = mission['ortho_s3_key']
    local_ortho = f"/tmp/processing/{vol_id}/orthomosaic.tif"
    os.makedirs(os.path.dirname(local_ortho), exist_ok=True)

    # Download orthomosaic from S3
    try:
        storage.download_file(ortho_s3_key, local_ortho)
    except Exception as dl_err:
        report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to download ortho from S3: {dl_err}")
        raise

    # Get detections — prefer DB-backed, fallback to in-memory
    raw_detections = mission.get('detections', [])
    if not raw_detections:
        try:
            with get_session() as session:
                db_dets = get_mission_detections(session, vol_id)
                for d in db_dets:
                    raw_detections.append({
                        'class_name': d.class_name,
                        'confidence': d.confidence,
                        'global_pixel_x': d.pixel_x,
                        'global_pixel_y': d.pixel_y,
                        'geo_lat': d.geo_lat,
                        'geo_lon': d.geo_lon,
                        'segment': d.segment,
                    })
        except Exception as db_err:
            logger.warning("Failed to load detections from DB for %s: %s", vol_id, db_err)

    raw_detection_count = len(raw_detections)
    deduped_detections = dedupe_mission_detections(raw_detections)

    output_path = f"/tmp/processing/{vol_id}/orthomosaic_annotated.tif"
    
    report_progress(
        vol_id,
        "FINAL_IMAGE",
        90,
        log=f"Generating annotated orthomosaic with {len(deduped_detections)} merged detections from {raw_detection_count} raw detections...",
    )
    try:
        with rasterio.open(local_ortho) as src:
            meta = src.meta.copy()
            data = src.read()
            
            # Data is usually (C, H, W). Rasterio expects RGB, we need HWC for OpenCV
            img = data[:3].transpose(1, 2, 0).copy() # C,H,W -> H,W,C
            
            # Dessiner les masques — use ROI-local overlay to avoid
            # copying the entire image per detection (OOM on large orthos).
            for det in deduped_detections:
                if 'segment' in det and len(det['segment']) > 0:
                    pts = np.array(det['segment'], np.int32)
                    pts = pts.reshape((-1, 1, 2))

                    # Compute bounding box of the polygon + margin for the local overlay
                    bx, by, bw, bh = cv2.boundingRect(pts)
                    margin = 4
                    rx0 = max(bx - margin, 0)
                    ry0 = max(by - margin, 0)
                    rx1 = min(bx + bw + margin, img.shape[1])
                    ry1 = min(by + bh + margin, img.shape[0])

                    # Draw transparent red overlay only on the ROI
                    roi = img[ry0:ry1, rx0:rx1]
                    roi_overlay = roi.copy()
                    pts_local = pts - np.array([rx0, ry0], dtype=np.int32)
                    cv2.fillPoly(roi_overlay, [pts_local], (255, 0, 0))
                    cv2.addWeighted(roi_overlay, 0.4, roi, 0.6, 0, roi)
                    del roi_overlay

                    # Draw the contour
                    cv2.polylines(img, [pts], True, (255, 0, 0), 2)
                    
                    # Draw center point / label
                    cx, cy = int(det['global_pixel_x']), int(det['global_pixel_y'])
                    cv2.circle(img, (cx, cy), 5, (0, 255, 0), -1)
                    gps_lines = format_detection_gps(det, mission)
                    draw_detection_label(img, cx, cy, gps_lines)
            
            # Back to C,H,W
            out_data = img.transpose(2, 0, 1)
            
            with rasterio.open(output_path, 'w', **meta) as dst:
                dst.write(out_data)

            # Free large arrays now that we're done writing
            del data, img, out_data
            import gc
            gc.collect()

        # Upload annotated ortho to S3
        annotated_s3_key = f"missions/{vol_id}/orthomosaic_annotated.tif"
        try:
            storage.upload_file(output_path, annotated_s3_key)
        except Exception as upload_err:
            logger.warning("Failed to upload annotated ortho to S3: %s", upload_err)

        report_progress(
            vol_id,
            "DONE",
            100,
            status="success",
            log=f"Annotated orthomosaic saved ({len(deduped_detections)} merged detections from {raw_detection_count} raw detections)",
        )

        # Clean up local temp files
        import shutil
        shutil.rmtree(f"/tmp/processing/{vol_id}", ignore_errors=True)
        
    except Exception as e:
        logger.exception("Failed to generate final image for %s", vol_id)
        report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to generate final image: {e}")
        raise
    finally:
        # Always clean up temp files to avoid filling the system disk.
        import shutil
        shutil.rmtree(f"/tmp/processing/{vol_id}", ignore_errors=True)

def slice_orthomosaic(ortho_s3_key, vol_id, tile_size=1024, classes=["car"], ai_confidence=0.3, ai_backend="yolo", ai_model_variant="yolo26l", sam_prompt="car"):
    """Download orthomosaic from S3, tile it locally, upload tiles to S3, and send Kafka messages."""
    ai_backend = normalize_ai_backend(ai_backend)

    # Download orthomosaic from S3 to local temp
    local_ortho = f"/tmp/processing/{vol_id}/orthomosaic.tif"
    os.makedirs(os.path.dirname(local_ortho), exist_ok=True)
    try:
        storage.download_file(ortho_s3_key, local_ortho)
    except Exception as dl_err:
        report_progress(vol_id, "ERROR", 0, status="error", log=f"Failed to download orthomosaic from S3: {dl_err}")
        raise

    tiles_dir = f"/tmp/processing/{vol_id}/tiles"
    os.makedirs(tiles_dir, exist_ok=True)
    cleanup_tiles_directory(tiles_dir)
    tiles_s3_prefix = f"missions/{vol_id}/tiles"
    
    report_progress(vol_id, "TILING_START", 0)

    try:
        with rasterio.open(local_ortho) as src:
            width = src.width
            height = src.height
            tile_overlap = max(0, min(tile_size // 2, int(os.getenv("TILE_OVERLAP", str(tile_size // 4)))))
            x_starts = build_tile_starts(width, tile_size, tile_overlap)
            y_starts = build_tile_starts(height, tile_size, tile_overlap)
            
            transform_list = list(src.transform.to_gdal()) if src.transform else None
            crs_str = src.crs.to_string() if src.crs else "unknown"
            
            missions.set(vol_id, {
                "ortho_s3_key": ortho_s3_key,
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
                    if cancel_manager.is_cancelled(vol_id):
                        logger.info("Tiling cancelled mid-loop for %s", vol_id)
                        shutil.rmtree(f"/tmp/processing/{vol_id}", ignore_errors=True)
                        return
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
                    
                    # Upload tile to S3
                    tile_s3_key = f"{tiles_s3_prefix}/{tile_filename}"
                    try:
                        storage.upload_file(tile_path, tile_s3_key)
                    except Exception as upload_err:
                        raise RuntimeError(
                            f"Failed to upload tile {tile_filename}: {upload_err}"
                        ) from upload_err

                    tile_msg = {
                        "vol_id": vol_id,
                        "tile_index": tile_index,
                        "tile_s3_key": tile_s3_key,
                        "offset_x": x,
                        "offset_y": y,
                        "ai_backend": ai_backend,
                        "ai_model_variant": ai_model_variant,
                        "sam_prompt": sam_prompt,
                        "classes": classes,
                        "ai_confidence": ai_confidence,
                        "total_tiles": total_tiles,
                        "ortho_transform": transform_list,
                        "ortho_crs": crs_str
                    }
                    tile_msg = make_event(
                        "image_tile",
                        tile_msg,
                        event_id=deterministic_event_id(
                            "image_tile",
                            vol_id,
                            tile_index,
                        ),
                        correlation_id=vol_id,
                    )
                    producer.produce(TOPIC_OUT_TILES, key=f"{vol_id}_{tile_index}", value=json.dumps(tile_msg))
                    
                    tile_index += 1
                    progress = int((tile_index / total_tiles) * 100)
                    if tile_index % 10 == 0:
                        report_progress(vol_id, "TILING_IN_PROGRESS", progress)
            
            # Update to exact count (may differ from estimate if loop was interrupted)
            missions.set_total_tiles(vol_id, tile_index)
            if producer.flush():
                raise RuntimeError("one or more tile events were not delivered")
            report_progress(vol_id, "TILING_DONE", 100, status="success")
            print(f"📦 Orthomosaïque découpée en {tile_index} tuiles pour le vol {vol_id}.")
            
    except Exception as e:
        logger.exception("Failed to tile orthomosaic for %s", vol_id)
        error_msg = f"Failed to tile orthomosaic: {str(e)}"
        report_progress(vol_id, "ERROR", 0, status="error", log=error_msg)
        print(f"❌ {error_msg}")
        shutil.rmtree(f"/tmp/processing/{vol_id}", ignore_errors=True)
        raise

def process_pipeline_event(data, topic):
    if topic == TOPIC_IN_ORTHO:
        vol_id = data['vol_id']
        cancel_manager.clear(vol_id)
        ortho_s3_key = data.get('ortho_s3_key') or data.get('ortho_path', '')
        slice_orthomosaic(
            ortho_s3_key,
            vol_id,
            classes=data.get('classes', ['car']),
            ai_confidence=data.get('ai_confidence', 0.3),
            ai_backend=normalize_ai_backend(data.get('ai_backend', 'yolo')),
            ai_model_variant=data.get('ai_model_variant', 'yolo26l'),
            sam_prompt=data.get('sam_prompt', 'car'),
        )
        return

    vol_id = data['vol_id']
    if cancel_manager.is_cancelled(vol_id):
        return

    tile_detections = data.get('detections', [])
    tile_index = data['tile_index']
    try:
        with get_session() as session:
            mission_obj = get_or_create_mission(session, vol_id)
            already_persisted = (
                session.query(DBDetection.id)
                .filter(
                    DBDetection.vol_id == vol_id,
                    DBDetection.tile_index == tile_index,
                )
                .first()
                is not None
            )
            if not already_persisted:
                for det in tile_detections:
                    session.add(
                        DBDetection(
                            mission_id=mission_obj.id,
                            vol_id=vol_id,
                            tile_index=tile_index,
                            class_name=det.get('class_name', 'unknown'),
                            class_id=det.get('class_id'),
                            confidence=float(det.get('confidence', 0)),
                            pixel_x=det.get('global_pixel_x'),
                            pixel_y=det.get('global_pixel_y'),
                            geo_lon=det.get('geo_lon'),
                            geo_lat=det.get('geo_lat'),
                            segment=det.get('segment'),
                        )
                    )
            mission_obj.tiles_received = count_received_tiles(session, vol_id)
    except Exception:
        logger.exception(
            "Failed to persist detections to DB for %s tile %s",
            vol_id,
            tile_index,
        )
        raise

    mission, is_complete = missions.record_tile_detections(
        vol_id,
        tile_index,
        tile_detections,
    )
    if mission is not None and is_complete:
        report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
        generate_final_ortho(vol_id, mission)
        missions.pop(vol_id)


def worker_main():
    work_consumer = create_work_consumer()
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    print("🎧 App 3 (Tiler/Aggregator) en attente...")
    try:
        while True:
            message = work_consumer.poll(1.0)
            if message is None or message.error():
                continue
            topic = message.topic()
            expected_type = (
                "orthomosaic"
                if topic == TOPIC_IN_ORTHO
                else "tile_detection"
            )
            process_message(
                consumer=work_consumer,
                producer=producer,
                message=message,
                consumer_group=CONSUMER_GROUP,
                expected_type=expected_type,
                dead_letter_topic=TOPIC_DEAD_LETTER,
                handler=lambda data, message_topic=topic: (
                    process_pipeline_event(data, message_topic)
                ),
                logger=logger,
            )
    except KeyboardInterrupt:
        pass
    finally:
        work_consumer.close()


if __name__ == "__main__":
    worker_main()
