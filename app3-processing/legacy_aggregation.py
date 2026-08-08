"""Durable aggregation for the initial mission pipeline compatibility path."""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

from geoalchemy2.elements import WKTElement

from processing_core import dedupe_mission_detections
from shared import storage
from shared.database import (
    Detection as DBDetection,
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


DetectionRecord = dict[str, Any]
JsonObject = dict[str, Any]


class MissionDescriptor(TypedDict):
    ortho_s3_key: str | None
    tiling_metadata: JsonObject


class ProgressReporter(Protocol):
    def __call__(
        self,
        vol_id: str,
        step: str,
        progress: int,
        status: str = "processing",
        log: str | None = None,
    ) -> None: ...


def dedupe_configured(
    detections: list[DetectionRecord],
) -> list[DetectionRecord]:
    """Apply the deployment-configured overlap thresholds."""

    return dedupe_mission_detections(
        detections,
        center_threshold=float(os.getenv("UNTILER_DEDUPE_CENTER_THRESHOLD", "40")),
        iou_threshold=float(os.getenv("UNTILER_DEDUPE_IOU_THRESHOLD", "0.05")),
    )


class LegacyAggregationWorkflow:
    """Persist and finalize detections emitted without an analysis run ID."""

    def __init__(
        self,
        *,
        report_progress: ProgressReporter,
        logger: logging.Logger,
    ) -> None:
        self.report_progress = report_progress
        self.logger = logger

    @staticmethod
    def _dedupe(detections: list[DetectionRecord]) -> list[DetectionRecord]:
        return dedupe_configured(detections)

    def _project_detection_geometry(
        self,
        detection: DetectionRecord,
        metadata: JsonObject,
        *,
        vol_id: str,
        tile_index: int,
    ) -> WKTElement | None:
        segment = cast(list[list[float]], detection.get("segment") or [])
        if len(segment) < 3 or not metadata.get("transform") or not metadata.get("crs"):
            return None
        try:
            ring = pixel_segment_to_wgs84(
                segment,
                geotransform=metadata["transform"],
                source_crs=metadata["crs"],
            )
            coordinates = ", ".join(f"{longitude} {latitude}" for longitude, latitude in ring)
            return WKTElement(f"POLYGON(({coordinates}))", srid=4326)
        except (TypeError, ValueError):
            self.logger.warning(
                "Unable to project detection polygon for %s tile %s",
                vol_id,
                tile_index,
            )
            return None

    def _store_detection(
        self,
        session: Any,
        mission: Any,
        detection: DetectionRecord,
        tile_index: int,
    ) -> None:
        metadata = cast(JsonObject, mission.tiling_metadata or {})
        session.add(
            DBDetection(
                mission_id=mission.id,
                vol_id=mission.vol_id,
                tile_index=tile_index,
                class_name=detection.get("class_name", "unknown"),
                class_id=detection.get("class_id"),
                confidence=float(detection.get("confidence", 0)),
                geometry=self._project_detection_geometry(
                    detection,
                    metadata,
                    vol_id=mission.vol_id,
                    tile_index=tile_index,
                ),
                pixel_x=detection.get("global_pixel_x"),
                pixel_y=detection.get("global_pixel_y"),
                geo_lon=detection.get("geo_lon"),
                geo_lat=detection.get("geo_lat"),
                segment=detection.get("segment") or [],
            )
        )

    def _store_tile(
        self,
        vol_id: str,
        tile_index: int,
        detections: list[DetectionRecord],
        expected_attempt: int,
    ) -> MissionDescriptor | None:
        finalize_mission: MissionDescriptor | None = None
        with get_session() as session:
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).with_for_update().first()
            if mission is None:
                mission = get_or_create_mission(session, vol_id)
            if int(mission.retry_count or 0) != expected_attempt:
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
                    self._store_detection(
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
                and mission.aggregation_status not in {"finalizing", "completed"}
            ):
                mission.aggregation_status = "finalizing"
                finalize_mission = {
                    "ortho_s3_key": mission.ortho_s3_key,
                    "tiling_metadata": cast(
                        JsonObject,
                        mission.tiling_metadata or {},
                    ),
                }
        return finalize_mission

    @staticmethod
    def _mark_failed(vol_id: str) -> None:
        with get_session() as session:
            mission = session.query(Mission).filter(Mission.vol_id == vol_id).with_for_update().first()
            if mission is not None:
                mission.aggregation_status = "failed"

    def generate_vector_results(
        self,
        vol_id: str,
        mission: MissionDescriptor,
    ) -> None:
        """Publish lightweight AI vectors; never duplicate the full raster."""

        with get_session() as session:
            db_detections = get_mission_detections(session, vol_id)
            raw_detections: list[DetectionRecord] = [
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

        deduped = self._dedupe(raw_detections)
        metadata = mission["tiling_metadata"]
        feature_collection = cast(
            JsonObject,
            detections_feature_collection(
                deduped,
                geotransform=metadata.get("transform"),
                source_crs=metadata.get("crs"),
                vol_id=vol_id,
            ),
        )
        output_directory = Path("/tmp/processing") / vol_id
        output_directory.mkdir(parents=True, exist_ok=True)
        try:
            output = output_directory / "detections.geojson"
            temporary = output.with_suffix(".geojson.tmp")
            temporary.write_text(
                json.dumps(feature_collection, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, output)
            storage.upload_verified_file(
                output,
                f"missions/{vol_id}/detections.geojson",
            )

            with get_session() as session:
                mission_object = session.query(Mission).filter(Mission.vol_id == vol_id).with_for_update().one()
                mission_object.aggregation_status = "completed"
                mission_object.aggregation_completed_at = datetime.now(UTC)

            self.report_progress(
                vol_id,
                "DONE",
                100,
                status="success",
                log=(
                    f"COG ready with {len(feature_collection['features'])} "
                    f"vector detections ({len(raw_detections)} raw)"
                ),
            )
        finally:
            shutil.rmtree(output_directory, ignore_errors=True)

    def process_detection(self, data: JsonObject) -> None:
        vol_id = cast(str, data["vol_id"])
        tile_index = int(data["tile_index"])
        try:
            finalize_mission = self._store_tile(
                vol_id,
                tile_index,
                cast(list[DetectionRecord], data.get("detections") or []),
                int(data.get("attempt", 0)),
            )
        except Exception:
            self.logger.exception(
                "Failed to persist detections to DB for %s tile %s",
                vol_id,
                tile_index,
            )
            raise

        if finalize_mission is None:
            return
        self.report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
        try:
            self.generate_vector_results(vol_id, finalize_mission)
        except Exception:
            self._mark_failed(vol_id)
            raise

    def recover(self) -> None:
        """Resume completed tile sets left behind by a crashed replica."""

        stale_before = datetime.now(UTC) - timedelta(minutes=10)
        ready: list[tuple[str, MissionDescriptor]] = []
        with get_session() as session:
            candidates = (
                session.query(Mission)
                .filter(
                    Mission.total_tiles.isnot(None),
                    Mission.tiles_received >= Mission.total_tiles,
                    (
                        Mission.aggregation_status.in_(("collecting", "failed"))
                        | ((Mission.aggregation_status == "finalizing") & (Mission.updated_at < stale_before))
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
                            "tiling_metadata": cast(
                                JsonObject,
                                mission.tiling_metadata or {},
                            ),
                        },
                    )
                )
        for vol_id, descriptor in ready:
            try:
                self.report_progress(vol_id, "AGGREGATING_DETECTIONS", 80)
                self.generate_vector_results(vol_id, descriptor)
            except Exception:
                self.logger.exception(
                    "Failed to recover aggregation for %s",
                    vol_id,
                )
                self._mark_failed(vol_id)
