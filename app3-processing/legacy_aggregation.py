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

from shared import storage
from shared.detection_geometry import dedupe_mission_detections
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
from shared.model_provenance import validate_model_manifest
from shared.tile_results import tile_result_s3_key, validate_tile_result_bytes
from shared.tenancy import (
    LEGACY_ORGANIZATION_ID,
    MissionObjectNamespace,
    mission_event_namespace,
)


DetectionRecord = dict[str, Any]
JsonObject = dict[str, Any]


class MissionDescriptor(TypedDict, total=False):
    ortho_s3_key: str | None
    tiling_metadata: JsonObject
    organization_id: str
    workspace_prefix: str


class TilePersistenceResult(TypedDict):
    finalize_mission: MissionDescriptor | None
    tiles_received: int
    total_tiles: int


class ProgressReporter(Protocol):
    def __call__(
        self,
        vol_id: str,
        step: str,
        progress: int,
        status: str = "processing",
        log: str | None = None,
        organization_id: str = "legacy-unassigned",
    ) -> None: ...


def dedupe_configured(
    detections: list[DetectionRecord],
) -> list[DetectionRecord]:
    """Apply the deployment-configured overlap thresholds."""

    return cast(
        list[DetectionRecord],
        dedupe_mission_detections(
            detections,
            center_threshold=float(
                os.getenv("UNTILER_DEDUPE_CENTER_THRESHOLD", "40")
            ),
            iou_threshold=float(os.getenv("UNTILER_DEDUPE_IOU_THRESHOLD", "0.05")),
        ),
    )


class LegacyAggregationWorkflow:
    """Persist and finalize detections emitted without an analysis run ID."""

    def __init__(
        self,
        *,
        report_progress: ProgressReporter,
        report_ia_progress: ProgressReporter,
        logger: logging.Logger,
        maximum_tile_result_bytes: int | None = None,
    ) -> None:
        self.report_progress = report_progress
        self.report_ia_progress = report_ia_progress
        self.logger = logger
        self.maximum_tile_result_bytes = max(
            1,
            maximum_tile_result_bytes
            if maximum_tile_result_bytes is not None
            else int(os.getenv("ANALYSIS_MAX_TILE_RESULT_BYTES", str(10 * 1024 * 1024))),
        )

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
        namespace: MissionObjectNamespace | None = None,
    ) -> TilePersistenceResult | None:
        namespace = namespace or MissionObjectNamespace.create(
            LEGACY_ORGANIZATION_ID,
            vol_id,
        )
        finalize_mission: MissionDescriptor | None = None
        with get_session() as session:
            mission = (
                session.query(Mission)
                .filter(
                    Mission.vol_id == vol_id,
                    Mission.organization_id == namespace.organization_id,
                )
                .with_for_update()
                .first()
            )
            if mission is None:
                mission = get_or_create_mission(
                    session,
                    vol_id,
                    organization_id=namespace.organization_id,
                    workspace_prefix=namespace.root,
                )
            durable_namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                mission.vol_id,
                mission.workspace_prefix,
            )
            if durable_namespace != namespace:
                raise RuntimeError(
                    "AI tile event namespace does not match the durable mission"
                )
            if int(mission.retry_count or 0) != expected_attempt:
                return None
            receipt = (
                session.query(ProcessedTile)
                .filter(
                    ProcessedTile.mission_id == mission.id,
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
            mission.tiles_received = count_received_tiles(
                session,
                vol_id,
                namespace.organization_id,
            )
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
                    "organization_id": mission.organization_id,
                    "workspace_prefix": mission.workspace_prefix,
                }
            result: TilePersistenceResult = {
                "finalize_mission": finalize_mission,
                "tiles_received": int(mission.tiles_received or 0),
                "total_tiles": int(mission.total_tiles or 0),
            }
        return result

    def _load_referenced_detections(
        self,
        data: JsonObject,
    ) -> list[DetectionRecord]:
        vol_id = cast(str, data["vol_id"])
        tile_index = int(data["tile_index"])
        attempt = int(data.get("attempt", 0))
        result_key = cast(str, data["result_s3_key"])
        namespace = mission_event_namespace(data)
        expected_key = tile_result_s3_key(
            vol_id,
            None,
            tile_index,
            attempt,
            organization_id=namespace.organization_id,
            workspace_prefix=namespace.root,
        )
        if result_key != expected_key:
            raise RuntimeError(
                "AI tile result key does not match the deterministic mission key"
            )
        expected_size = int(data["result_size_bytes"])
        if expected_size > self.maximum_tile_result_bytes:
            raise RuntimeError(
                f"AI tile result exceeds the {self.maximum_tile_result_bytes}-byte limit: {result_key}"
            )
        stream, content_length, _ = storage.get_object_stream(result_key)
        content_length = int(content_length or 0)
        if content_length != expected_size:
            stream.close()
            raise RuntimeError(
                f"AI tile result size differs from its reference: "
                f"{content_length}/{expected_size} bytes for {result_key}"
            )
        try:
            raw_payload = cast(bytes, stream.read(self.maximum_tile_result_bytes + 1))
        finally:
            stream.close()
        if len(raw_payload) > self.maximum_tile_result_bytes:
            raise RuntimeError(
                f"AI tile result exceeds the {self.maximum_tile_result_bytes}-byte limit: {result_key}"
            )
        manifest = validate_model_manifest(data.get("model_manifest"))
        artifact = validate_tile_result_bytes(
            raw_payload,
            expected_sha256=cast(str, data["result_sha256"]),
            expected_size=expected_size,
            vol_id=vol_id,
            analysis_run_id=None,
            tile_index=tile_index,
            attempt=attempt,
            detection_count=int(data["detection_count"]),
            model_manifest=manifest,
        )
        return cast(list[DetectionRecord], artifact.raw_detections)

    def _event_detections(self, data: JsonObject) -> list[DetectionRecord]:
        if data.get("result_s3_key") is not None:
            return self._load_referenced_detections(data)
        return cast(list[DetectionRecord], data.get("detections") or [])

    @staticmethod
    def _mark_failed(
        vol_id: str,
        organization_id: str = LEGACY_ORGANIZATION_ID,
    ) -> None:
        with get_session() as session:
            mission = (
                session.query(Mission)
                .filter(
                    Mission.vol_id == vol_id,
                    Mission.organization_id == organization_id,
                )
                .with_for_update()
                .first()
            )
            if mission is not None:
                mission.aggregation_status = "failed"

    def generate_vector_results(
        self,
        vol_id: str,
        mission: MissionDescriptor,
    ) -> None:
        """Publish lightweight AI vectors; never duplicate the full raster."""

        with get_session() as session:
            organization_id = mission.get(
                "organization_id",
                LEGACY_ORGANIZATION_ID,
            )
            db_detections = get_mission_detections(
                session,
                vol_id,
                organization_id,
            )
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
            namespace = mission_event_namespace(
                {**mission, "vol_id": vol_id}
            )
            storage.upload_verified_file(
                output,
                namespace.key("detections.geojson"),
            )

            with get_session() as session:
                mission_object = (
                    session.query(Mission)
                    .filter(
                        Mission.vol_id == vol_id,
                        Mission.organization_id == namespace.organization_id,
                    )
                    .with_for_update()
                    .one()
                )
                mission_object.aggregation_status = "completed"
                mission_object.aggregation_completed_at = datetime.now(UTC)

            summary = (
                f"IA durably completed all tiles with "
                f"{len(feature_collection['features'])} vector detections "
                f"({len(raw_detections)} raw)"
            )
            self.report_ia_progress(
                vol_id,
                "DETECTING",
                100,
                status="success",
                log=summary,
                organization_id=namespace.organization_id,
            )
            self.report_progress(
                vol_id,
                "DONE",
                100,
                status="success",
                log=(
                    f"COG ready with {len(feature_collection['features'])} "
                    f"vector detections ({len(raw_detections)} raw)"
                ),
                organization_id=namespace.organization_id,
            )
        finally:
            shutil.rmtree(output_directory, ignore_errors=True)

    def process_detection(self, data: JsonObject) -> None:
        vol_id = cast(str, data["vol_id"])
        namespace = mission_event_namespace(data)
        tile_index = int(data["tile_index"])
        try:
            persistence = self._store_tile(
                vol_id,
                tile_index,
                self._event_detections(data),
                int(data.get("attempt", 0)),
                namespace,
            )
        except Exception:
            self.logger.exception(
                "Failed to persist detections to DB for %s tile %s",
                vol_id,
                tile_index,
            )
            raise

        if persistence is None:
            return
        tiles_received = persistence["tiles_received"]
        total_tiles = persistence["total_tiles"]
        if (
            tiles_received == 1
            or tiles_received % 10 == 0
            or (total_tiles and tiles_received >= total_tiles)
        ):
            progress = min(
                99,
                int(100 * tiles_received / max(total_tiles, 1)),
            )
            self.report_ia_progress(
                vol_id,
                "DETECTING",
                progress,
                log=f"Durably received {tiles_received}/{total_tiles} IA tile results",
                organization_id=namespace.organization_id,
            )

        finalize_mission = persistence["finalize_mission"]
        if finalize_mission is None:
            return
        self.report_progress(
            vol_id,
            "AGGREGATING_DETECTIONS",
            80,
            organization_id=namespace.organization_id,
        )
        try:
            self.generate_vector_results(vol_id, finalize_mission)
        except Exception as error:
            self._mark_failed(vol_id, namespace.organization_id)
            self.report_ia_progress(
                vol_id,
                "ERROR",
                0,
                status="error",
                log=f"IA aggregation failed: {error}",
                organization_id=namespace.organization_id,
            )
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
                            "organization_id": mission.organization_id,
                            "workspace_prefix": mission.workspace_prefix,
                        },
                    )
                )
        for vol_id, descriptor in ready:
            organization_id = descriptor.get(
                "organization_id",
                "legacy-unassigned",
            )
            try:
                self.report_progress(
                    vol_id,
                    "AGGREGATING_DETECTIONS",
                    80,
                    organization_id=organization_id,
                )
                self.generate_vector_results(vol_id, descriptor)
            except Exception as error:
                self.logger.exception(
                    "Failed to recover aggregation for %s",
                    vol_id,
                )
                self._mark_failed(vol_id, organization_id)
                self.report_ia_progress(
                    vol_id,
                    "ERROR",
                    0,
                    status="error",
                    log=f"IA aggregation recovery failed: {error}",
                    organization_id=organization_id,
                )
