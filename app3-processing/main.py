import json
import logging
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
from confluent_kafka import Consumer, Producer
from geoalchemy2.elements import WKTElement
from rasterio.warp import transform as warp_transform

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from analysis_workflow import AnalysisWorkflow
from orthomosaic_tiler import OrthomosaicTiler
from processing_core import dedupe_mission_detections as dedupe_detection_core

from shared import storage
from shared.config import (
    KAFKA_BROKER,
    TOPIC_CONTROL,
    TOPIC_DEAD_LETTER,
    TOPIC_IMAGE_TILES,
    TOPIC_ORTHO,
    TOPIC_STATUS,
    TOPIC_TILE_DETECTIONS,
)
from shared.cancellation import AttemptCancellationRegistry
from shared.database import (
    Detection as DBDetection,
)
from shared.database import (
    Mission,
    ProcessedTile,
    count_received_tiles,
    get_mission_detections,
    get_or_create_mission,
    get_session,
)
from shared.geospatial_assets import (
    detections_feature_collection,
    pixel_segment_to_wgs84,
)
from shared.kafka_reliability import (
    process_message,
    reliable_consumer_config,
)
from shared.worker_messaging import (
    make_cancellation_handler,
    make_progress_publisher,
    run_control_consumer,
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

cancel_manager = AttemptCancellationRegistry()

def control_consumer_thread():
    run_control_consumer(
        kafka_broker=KAFKA_BROKER,
        topic=TOPIC_CONTROL,
        consumer_group="processing-control-workers",
        producer=producer,
        dead_letter_topic=TOPIC_DEAD_LETTER,
        handler=make_cancellation_handler(cancel_manager, logger),
        logger=logger,
    )

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


analysis_workflow = AnalysisWorkflow(
    producer=producer,
    orthomosaic_topic=TOPIC_IN_ORTHO,
    tile_topic=TOPIC_OUT_TILES,
    dedupe=dedupe_mission_detections,
    logger=logger,
)
orthomosaic_tiler = OrthomosaicTiler(
    producer=producer,
    tile_topic=TOPIC_OUT_TILES,
    is_cancelled=cancel_manager.is_cancelled,
    report_progress=report_progress,
    logger=logger,
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

def generate_vector_results(vol_id, mission):
    """Publish lightweight AI vectors; never duplicate the full raster."""

    with get_session() as session:
        db_detections = get_mission_detections(session, vol_id)
        raw_detections = [
            {
                "id": detection.id,
                "tile_index": detection.tile_index,
                "class_name": detection.class_name,
                "class_id": detection.class_id,
                "confidence": detection.confidence,
                "global_pixel_x": detection.pixel_x,
                "global_pixel_y": detection.pixel_y,
                "geo_lat": detection.geo_lat,
                "geo_lon": detection.geo_lon,
                "segment": detection.segment,
            }
            for detection in db_detections
        ]

    deduped = dedupe_mission_detections(raw_detections)
    metadata = mission.get("tiling_metadata") or {}
    feature_collection = detections_feature_collection(
        deduped,
        geotransform=metadata.get("transform"),
        source_crs=metadata.get("crs"),
        vol_id=vol_id,
    )
    output_directory = Path(f"/tmp/processing/{vol_id}")
    output_directory.mkdir(parents=True, exist_ok=True)
    output = output_directory / "detections.geojson"
    temporary = output.with_suffix(".geojson.tmp")
    temporary.write_text(
        json.dumps(feature_collection, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(temporary, output)
    vector_key = f"missions/{vol_id}/detections.geojson"
    storage.upload_verified_file(output, vector_key)

    with get_session() as session:
        mission_object = (
            session.query(Mission)
            .filter(Mission.vol_id == vol_id)
            .with_for_update()
            .one()
        )
        mission_object.aggregation_status = "completed"
        mission_object.aggregation_completed_at = datetime.now(timezone.utc)

    report_progress(
        vol_id,
        "DONE",
        100,
        status="success",
        log=(
            f"COG ready with {len(feature_collection['features'])} "
            f"vector detections ({len(raw_detections)} raw)"
        ),
    )
    shutil.rmtree(output_directory, ignore_errors=True)

def _process_orthomosaic(data):
    vol_id = data["vol_id"]
    analysis_run_id = data.get("analysis_run_id")
    analysis_attempt = int(data.get("attempt", 0))
    cancel_manager.clear(vol_id, analysis_run_id, analysis_attempt)
    ortho_s3_key = data.get("ortho_s3_key") or data.get(
        "ortho_path",
        "",
    )
    orthomosaic_tiler.slice(
        ortho_s3_key,
        vol_id,
        tile_size=int(data.get("tile_size", 1024)),
        classes=data.get("classes", ["car"]),
        ai_confidence=data.get("ai_confidence", 0.3),
        ai_backend=data.get("ai_backend", "yolo"),
        ai_model_variant=data.get("ai_model_variant", "yolo26l"),
        sam_prompt=data.get("sam_prompt", "car"),
        analysis_run_id=analysis_run_id,
        analysis_attempt=analysis_attempt,
    )


def _project_detection_geometry(
    detection,
    metadata,
    *,
    vol_id,
    tile_index,
):
    segment = detection.get("segment") or []
    if (
        len(segment) < 3
        or not metadata.get("transform")
        or not metadata.get("crs")
    ):
        return None
    try:
        ring = pixel_segment_to_wgs84(
            segment,
            geotransform=metadata["transform"],
            source_crs=metadata["crs"],
        )
        coordinates = ", ".join(
            f"{longitude} {latitude}"
            for longitude, latitude in ring
        )
        return WKTElement(f"POLYGON(({coordinates}))", srid=4326)
    except (TypeError, ValueError):
        logger.warning(
            "Unable to project detection polygon for %s tile %s",
            vol_id,
            tile_index,
        )
        return None


def _store_detection(session, mission, detection, tile_index):
    metadata = mission.tiling_metadata or {}
    segment = detection.get("segment") or []
    session.add(
        DBDetection(
            mission_id=mission.id,
            vol_id=mission.vol_id,
            tile_index=tile_index,
            class_name=detection.get("class_name", "unknown"),
            class_id=detection.get("class_id"),
            confidence=float(detection.get("confidence", 0)),
            geometry=_project_detection_geometry(
                detection,
                metadata,
                vol_id=mission.vol_id,
                tile_index=tile_index,
            ),
            pixel_x=detection.get("global_pixel_x"),
            pixel_y=detection.get("global_pixel_y"),
            geo_lon=detection.get("geo_lon"),
            geo_lat=detection.get("geo_lat"),
            segment=segment,
        )
    )


def _store_legacy_tile(
    vol_id,
    tile_index,
    detections,
    expected_attempt,
):
    finalize_mission = None
    with get_session() as session:
        mission = (
            session.query(Mission)
            .filter(Mission.vol_id == vol_id)
            .with_for_update()
            .first()
        )
        if mission is None:
            mission = get_or_create_mission(session, vol_id)
        if int(mission.retry_count or 0) != int(expected_attempt):
            return None
        receipt = (
            session.query(ProcessedTile)
            .filter(
                ProcessedTile.vol_id == vol_id,
                ProcessedTile.tile_index == tile_index,
            )
            .first()
        )
        if receipt is None:
            session.add(
                ProcessedTile(
                    mission_id=mission.id,
                    vol_id=vol_id,
                    tile_index=tile_index,
                    detection_count=len(detections),
                )
            )
            for detection in detections:
                _store_detection(
                    session,
                    mission,
                    detection,
                    tile_index,
                )
            session.flush()
        mission.tiles_received = count_received_tiles(session, vol_id)
        if (
            mission.total_tiles is not None
            and mission.tiles_received >= mission.total_tiles
            and mission.aggregation_status
            not in {"finalizing", "completed"}
        ):
            mission.aggregation_status = "finalizing"
            finalize_mission = {
                "ortho_s3_key": mission.ortho_s3_key,
                "tiling_metadata": mission.tiling_metadata or {},
            }
    return finalize_mission


def _mark_legacy_aggregation_failed(vol_id):
    with get_session() as session:
        mission = (
            session.query(Mission)
            .filter(Mission.vol_id == vol_id)
            .with_for_update()
            .first()
        )
        if mission is not None:
            mission.aggregation_status = "failed"


def _process_legacy_detection(data):
    vol_id = data["vol_id"]
    tile_index = data["tile_index"]
    try:
        finalize_mission = _store_legacy_tile(
            vol_id,
            tile_index,
            data.get("detections", []),
            data.get("attempt", 0),
        )
    except Exception:
        logger.exception(
            "Failed to persist detections to DB for %s tile %s",
            vol_id,
            tile_index,
        )
        raise

    if finalize_mission is None:
        return
    report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
    try:
        generate_vector_results(vol_id, finalize_mission)
    except Exception:
        _mark_legacy_aggregation_failed(vol_id)
        raise


def process_pipeline_event(data, topic):
    if topic == TOPIC_IN_ORTHO:
        _process_orthomosaic(data)
        return
    vol_id = data["vol_id"]
    analysis_run_id = data.get("analysis_run_id")
    if cancel_manager.is_cancelled(
        vol_id,
        analysis_run_id,
        data.get("attempt", 0),
    ):
        return
    if analysis_run_id:
        analysis_workflow.process_detection(data)
        return
    _process_legacy_detection(data)


def recover_ready_aggregations():
    """Resume completed tile sets left behind by a crashed replica."""

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=10)
    ready = []
    with get_session() as session:
        candidates = (
            session.query(Mission)
            .filter(
                Mission.total_tiles.isnot(None),
                Mission.tiles_received >= Mission.total_tiles,
                (
                    Mission.aggregation_status.in_(("collecting", "failed"))
                    | (
                        (Mission.aggregation_status == "finalizing")
                        & (Mission.updated_at < stale_before)
                    )
                ),
            )
            .with_for_update(skip_locked=True)
            .limit(10)
            .all()
        )
        for mission in candidates:
            mission.aggregation_status = "finalizing"
            ready.append(
                (
                    mission.vol_id,
                    {
                        "ortho_s3_key": mission.ortho_s3_key,
                        "tiling_metadata": mission.tiling_metadata or {},
                    },
                )
            )
    for vol_id, descriptor in ready:
        try:
            report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
            generate_vector_results(vol_id, descriptor)
        except Exception:
            logger.exception(
                "Failed to recover aggregation for %s",
                vol_id,
            )
            with get_session() as session:
                mission = (
                    session.query(Mission)
                    .filter(Mission.vol_id == vol_id)
                    .with_for_update()
                    .first()
                )
                if mission is not None:
                    mission.aggregation_status = "failed"


def worker_main():
    work_consumer = create_work_consumer()
    threading.Thread(target=control_consumer_thread, daemon=True).start()
    print("🎧 App 3 (Tiler/Aggregator) en attente...")
    last_recovery = 0.0
    try:
        while True:
            message = work_consumer.poll(1.0)
            if time.monotonic() - last_recovery >= 60:
                recover_ready_aggregations()
                analysis_workflow.recover()
                last_recovery = time.monotonic()
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
