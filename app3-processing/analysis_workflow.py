"""Durable AI campaign aggregation and recovery.

This module owns campaign state transitions and object-store publication. The
Kafka worker only dispatches events and injects its producer/topics.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable
from datetime import datetime, timedelta, UTC
from pathlib import Path
from uuid import uuid4

from geoalchemy2.elements import WKTElement
from sqlalchemy import func

from shared import storage
from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    MapFeature,
    get_session,
)
from shared.event_contracts import deterministic_event_id, make_event
from shared.geospatial_assets import detections_feature_collection
from shared.model_provenance import validate_model_manifest


class AnalysisWorkflow:
    """Idempotent campaign service used by the processing worker."""

    def __init__(
        self,
        *,
        producer,
        orthomosaic_topic: str,
        tile_topic: str,
        dedupe: Callable[[list[dict]], list[dict]],
        logger,
        maximum_tile_attempts: int | None = None,
        finalization_lease_seconds: int | None = None,
        finalization_owner: str | None = None,
        maximum_tile_result_bytes: int | None = None,
        maximum_aggregate_result_bytes: int | None = None,
        maximum_raw_detections: int | None = None,
        maximum_final_detections: int | None = None,
    ):
        self.producer = producer
        self.orthomosaic_topic = orthomosaic_topic
        self.tile_topic = tile_topic
        self.dedupe = dedupe
        self.logger = logger
        configured_attempts = (
            maximum_tile_attempts
            if maximum_tile_attempts is not None
            else int(os.getenv("ANALYSIS_MAX_TILE_ATTEMPTS", "5"))
        )
        self.maximum_tile_attempts = max(1, configured_attempts)
        configured_lease = (
            finalization_lease_seconds
            if finalization_lease_seconds is not None
            else int(os.getenv("ANALYSIS_FINALIZATION_LEASE_SECONDS", "1800"))
        )
        self.finalization_lease_seconds = max(60, configured_lease)
        self.finalization_owner = finalization_owner or (f"{socket.gethostname()}:{os.getpid()}:{uuid4()}")
        self.maximum_tile_result_bytes = max(
            1,
            maximum_tile_result_bytes
            if maximum_tile_result_bytes is not None
            else int(
                os.getenv(
                    "ANALYSIS_MAX_TILE_RESULT_BYTES",
                    str(10 * 1024 * 1024),
                )
            ),
        )
        self.maximum_aggregate_result_bytes = max(
            self.maximum_tile_result_bytes,
            maximum_aggregate_result_bytes
            if maximum_aggregate_result_bytes is not None
            else int(
                os.getenv(
                    "ANALYSIS_MAX_AGGREGATE_RESULT_BYTES",
                    str(256 * 1024 * 1024),
                )
            ),
        )
        self.maximum_raw_detections = max(
            1,
            maximum_raw_detections
            if maximum_raw_detections is not None
            else int(os.getenv("ANALYSIS_MAX_RAW_DETECTIONS", "100000")),
        )
        self.maximum_final_detections = max(
            1,
            maximum_final_detections
            if maximum_final_detections is not None
            else int(os.getenv("ANALYSIS_MAX_FINAL_DETECTIONS", "50000")),
        )

    @staticmethod
    def _styled_collection(detections, *, vol_id, run, tile_index=None):
        records = []
        for detection in detections:
            record = dict(detection)
            if tile_index is not None:
                record["tile_index"] = tile_index
            records.append(record)
        metadata = run.tiling_metadata or {}
        collection = detections_feature_collection(
            records,
            geotransform=metadata.get("transform"),
            source_crs=metadata.get("crs"),
            vol_id=vol_id,
        )
        for feature in collection["features"]:
            feature["properties"].update(
                {
                    "source": "ai",
                    "run_id": run.run_id,
                    "name": run.name,
                    "description": run.description or "",
                    "color": run.color,
                    "tags": run.tags or [],
                }
            )
        collection["properties"].update(
            {
                "run_id": run.run_id,
                "name": run.name,
                "color": run.color,
                "model_manifest": run.model_manifest,
            }
        )
        return collection

    @staticmethod
    def _write_verified_json(payload, key, local_path):
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, path)
        storage.upload_verified_file(path, key)

    @staticmethod
    def _feature_wkt(geometry):
        geometry_type = geometry.get("type")
        coordinates = geometry.get("coordinates")
        if geometry_type == "Point":
            return WKTElement(
                f"POINT({coordinates[0]} {coordinates[1]})",
                srid=4326,
            )
        if geometry_type == "Polygon":
            rings = ["(" + ", ".join(f"{point[0]} {point[1]}" for point in ring) + ")" for ring in coordinates]
            return WKTElement(f"POLYGON({', '.join(rings)})", srid=4326)
        raise ValueError(f"Unsupported AI geometry: {geometry_type}")

    @staticmethod
    def _run_descriptor(run):
        return {
            "id": run.id,
            "run_id": run.run_id,
            "vol_id": run.vol_id,
            "persist_results": bool(run.persist_results),
            "name": run.name,
            "description": run.description or "",
            "color": run.color,
            "tags": run.tags or [],
            "tiling_metadata": run.tiling_metadata or {},
            "model_manifest": run.model_manifest,
        }

    @staticmethod
    def _descriptor_proxy(descriptor):
        return type(
            "AnalysisDescriptor",
            (),
            {
                "run_id": descriptor["run_id"],
                "name": descriptor["name"],
                "description": descriptor["description"],
                "color": descriptor["color"],
                "tags": descriptor["tags"],
                "tiling_metadata": descriptor["tiling_metadata"],
                "model_manifest": descriptor["model_manifest"],
            },
        )()

    def _read_tile_payload(self, tile_key, total_payload_bytes):
        stream, content_length, _ = storage.get_object_stream(tile_key)
        content_length = int(content_length or 0)
        if content_length > self.maximum_tile_result_bytes:
            stream.close()
            raise RuntimeError(f"AI tile result exceeds the {self.maximum_tile_result_bytes}-byte limit: {tile_key}")
        if total_payload_bytes + content_length > self.maximum_aggregate_result_bytes:
            stream.close()
            raise RuntimeError(
                f"AI analysis exceeds the aggregate result size limit ({self.maximum_aggregate_result_bytes} bytes)"
            )
        try:
            raw_payload = stream.read(self.maximum_tile_result_bytes + 1)
        finally:
            stream.close()
        if len(raw_payload) > self.maximum_tile_result_bytes:
            raise RuntimeError(f"AI tile result exceeds the {self.maximum_tile_result_bytes}-byte limit: {tile_key}")
        return raw_payload, content_length

    def _load_tile_payloads(self, tile_keys):
        detections = []
        total_payload_bytes = 0
        for tile_key in tile_keys:
            if not tile_key:
                continue
            raw_payload, _ = self._read_tile_payload(
                tile_key,
                total_payload_bytes,
            )
            total_payload_bytes += len(raw_payload)
            if total_payload_bytes > self.maximum_aggregate_result_bytes:
                raise RuntimeError(
                    f"AI analysis exceeds the aggregate result size limit ({self.maximum_aggregate_result_bytes} bytes)"
                )
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                raise RuntimeError(f"AI tile result must be an object: {tile_key}")
            tile_detections = payload.get("raw_detections", [])
            if not isinstance(tile_detections, list):
                raise RuntimeError(f"AI tile result has invalid raw_detections: {tile_key}")
            if any(not isinstance(item, dict) for item in tile_detections):
                raise RuntimeError(f"AI tile result contains a non-object detection: {tile_key}")
            if len(detections) + len(tile_detections) > self.maximum_raw_detections:
                raise RuntimeError(
                    f"AI analysis exceeds the raw detection safety limit ({self.maximum_raw_detections})"
                )
            detections.extend(tile_detections)
        return detections

    def _replace_persisted_features(self, session, run, collection):
        session.query(MapFeature).filter(MapFeature.analysis_run_id == run.id).delete(synchronize_session=False)
        for feature in collection["features"]:
            properties = feature.get("properties") or {}
            session.add(
                MapFeature(
                    mission_id=run.mission_id,
                    analysis_run_id=run.id,
                    vol_id=run.vol_id,
                    source="ai",
                    geometry=self._feature_wkt(feature["geometry"]),
                    name=run.name,
                    description=run.description,
                    color=run.color,
                    tags=run.tags or [],
                    properties={
                        "backend": run.backend,
                        "model_variant": run.model_variant,
                    },
                    class_name=properties.get("class_name"),
                    confidence=properties.get("confidence"),
                    tile_index=properties.get("tile_index"),
                    created_by=run.created_by,
                )
            )

    @staticmethod
    def _lease_is_active(run, now):
        lease_until = run.finalization_lease_until
        if lease_until is None:
            return False
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=UTC)
        return lease_until > now

    def _claim_finalization(self, run_id):
        now = datetime.now(UTC)
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).with_for_update().first()
            if run is None or run.status in {"cancelled", "completed"} or self._lease_is_active(run, now):
                return None
            tiles = (
                session.query(AIAnalysisTile)
                .filter(
                    AIAnalysisTile.analysis_run_id == run.id,
                    AIAnalysisTile.status == "completed",
                )
                .order_by(AIAnalysisTile.tile_index)
                .all()
            )
            if not run.total_tiles or len(tiles) < run.total_tiles:
                return None
            run.status = "finalizing"
            run.phase = "deduplicating"
            run.finalization_owner = self.finalization_owner
            run.finalization_lease_until = now + timedelta(seconds=self.finalization_lease_seconds)
            run.heartbeat_at = now
            descriptor = self._run_descriptor(run)
            tile_keys = [tile.result_s3_key for tile in tiles]
            return descriptor, tile_keys

    def finalize(self, run_id):
        claim = self._claim_finalization(run_id)
        if claim is None:
            return False
        descriptor, tile_keys = claim

        raw = self._load_tile_payloads(tile_keys)
        unique = self.dedupe(raw)
        if len(unique) > self.maximum_final_detections:
            raise RuntimeError(
                f"AI analysis exceeds the final detection safety limit ({self.maximum_final_detections})"
            )
        collection = self._styled_collection(
            unique,
            vol_id=descriptor["vol_id"],
            run=self._descriptor_proxy(descriptor),
        )
        result_key = f"missions/{descriptor['vol_id']}/analyses/{descriptor['run_id']}/detections.geojson"
        self._write_verified_json(
            collection,
            result_key,
            (f"/tmp/processing/{descriptor['vol_id']}/{descriptor['run_id']}/detections.geojson"),
        )

        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).with_for_update().one()
            if run.status == "cancelled":
                return False
            if run.finalization_owner != self.finalization_owner:
                return False
            if descriptor["persist_results"]:
                self._replace_persisted_features(session, run, collection)
            run.result_s3_key = result_key
            run.detection_count = len(collection["features"])
            run.tiles_completed = len(tile_keys)
            run.status = "completed"
            run.phase = "completed"
            run.progress = 100
            run.completed_at = datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            run.error_message = None
            run.finalization_owner = None
            run.finalization_lease_until = None
        return True

    @staticmethod
    def _get_tile_context(session, vol_id, run_id, tile_index):
        run = (
            session.query(AIAnalysisRun)
            .filter(
                AIAnalysisRun.run_id == run_id,
                AIAnalysisRun.vol_id == vol_id,
            )
            .with_for_update()
            .first()
        )
        if run is None:
            raise RuntimeError(f"Unknown AI analysis run: {run_id}")
        receipt = (
            session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.tile_index == tile_index,
            )
            .with_for_update()
            .first()
        )
        if receipt is None:
            raise RuntimeError(f"Missing analysis tile journal: {run_id}/{tile_index}")
        return run, receipt

    @staticmethod
    def _resume_finalization_if_ready(session, run, receipt):
        if receipt.status != "completed":
            return False
        completed = (
            session.query(AIAnalysisTile)
            .filter(
                AIAnalysisTile.analysis_run_id == run.id,
                AIAnalysisTile.status == "completed",
            )
            .count()
        )
        if (
            run.status == "completed"
            or not run.total_tiles
            or completed < run.total_tiles
            or AnalysisWorkflow._lease_is_active(
                run,
                datetime.now(UTC),
            )
        ):
            return False
        run.status = "finalizing"
        run.phase = "recovery_finalizing"
        run.heartbeat_at = datetime.now(UTC)
        return True

    def _stage_tile_result(self, data, run):
        vol_id = data["vol_id"]
        run_id = data["analysis_run_id"]
        tile_index = int(data["tile_index"])
        detections = data.get("detections") or []
        collection = self._styled_collection(
            detections,
            vol_id=vol_id,
            run=run,
            tile_index=tile_index,
        )
        collection["raw_detections"] = [{**detection, "tile_index": tile_index} for detection in detections]
        result_key = f"missions/{vol_id}/analyses/{run_id}/results/tile_{tile_index}.geojson"
        self._write_verified_json(
            collection,
            result_key,
            (f"/tmp/processing/{vol_id}/{run_id}/results/tile_{tile_index}.geojson"),
        )
        return result_key, len(detections)

    @staticmethod
    def _mark_tile_complete(
        run_id,
        tile_index,
        result_key,
        count,
        expected_attempt,
    ):
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).with_for_update().one()
            receipt = (
                session.query(AIAnalysisTile)
                .filter(
                    AIAnalysisTile.analysis_run_id == run.id,
                    AIAnalysisTile.tile_index == tile_index,
                )
                .with_for_update()
                .one()
            )
            if run.status == "cancelled" or int(run.retry_count or 0) != int(expected_attempt):
                return False
            if receipt.status != "completed":
                receipt.status = "completed"
                receipt.result_s3_key = result_key
                receipt.detection_count = count
                receipt.completed_at = datetime.now(UTC)
            run.tiles_completed = (
                session.query(AIAnalysisTile)
                .filter(
                    AIAnalysisTile.analysis_run_id == run.id,
                    AIAnalysisTile.status == "completed",
                )
                .count()
            )
            run.detection_count = (
                session.query(
                    func.coalesce(
                        func.sum(AIAnalysisTile.detection_count),
                        0,
                    )
                )
                .filter(AIAnalysisTile.analysis_run_id == run.id)
                .scalar()
            )
            run.progress = min(
                99,
                int(100 * run.tiles_completed / max(run.total_tiles, 1)),
            )
            run.status = "running"
            run.phase = "detecting"
            run.heartbeat_at = datetime.now(UTC)
            if run.total_tiles and run.tiles_completed >= run.total_tiles:
                run.status = "finalizing"
                run.phase = "deduplicating"
                return True
            return False

    def _mark_finalization_failed(self, run_id, error):
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).with_for_update().first()
            if run is not None and run.finalization_owner in {
                None,
                self.finalization_owner,
            }:
                run.status = "failed"
                run.phase = "finalization_failed"
                run.error_message = str(error)
                run.heartbeat_at = datetime.now(UTC)
                run.finalization_owner = None
                run.finalization_lease_until = None

    def process_detection(self, data):
        vol_id = data["vol_id"]
        run_id = data["analysis_run_id"]
        tile_index = int(data["tile_index"])
        with get_session() as session:
            run, receipt = self._get_tile_context(session, vol_id, run_id, tile_index)
            event_attempt = int(data.get("attempt", 0))
            if run.status == "cancelled" or int(run.retry_count or 0) != event_attempt:
                return
            manifest = validate_model_manifest(data.get("model_manifest"))
            if manifest["backend"] != run.backend:
                raise RuntimeError("AI result model provenance backend does not match the analysis run")
            if run.model_manifest is None:
                run.model_manifest = manifest
            elif run.model_manifest != manifest:
                raise RuntimeError("AI analysis received results from different model provenance")
            resume_finalization = self._resume_finalization_if_ready(session, run, receipt)
            if receipt.status == "completed" and not resume_finalization:
                return
            if not resume_finalization:
                run_descriptor = self._descriptor_proxy(self._run_descriptor(run))
        if resume_finalization:
            try:
                self.finalize(run_id)
            except Exception as error:
                self._mark_finalization_failed(run_id, error)
                raise
            return
        result_key, count = self._stage_tile_result(data, run_descriptor)
        if not self._mark_tile_complete(
            run_id,
            tile_index,
            result_key,
            count,
            event_attempt,
        ):
            return
        try:
            self.finalize(run_id)
        except Exception as error:
            self._mark_finalization_failed(run_id, error)
            raise

    @staticmethod
    def _orthomosaic_recovery_event(run):
        return make_event(
            "orthomosaic",
            {
                "vol_id": run.vol_id,
                "ortho_s3_key": run.ortho_s3_key,
                "analysis_run_id": run.run_id,
                "classes": run.classes or [],
                "ai_confidence": run.confidence,
                "ai_backend": run.backend,
                "ai_model_variant": run.model_variant,
                "sam_prompt": run.prompt,
                "tile_size": run.tile_size,
            },
            event_id=deterministic_event_id(
                "orthomosaic",
                run.vol_id,
                run.run_id,
                run.retry_count,
            ),
            correlation_id=run.run_id,
            attempt=run.retry_count,
        )

    @staticmethod
    def _tile_recovery_event(run, tile):
        metadata = run.tiling_metadata or {}
        return make_event(
            "image_tile",
            {
                "vol_id": run.vol_id,
                "analysis_run_id": run.run_id,
                "tile_index": tile.tile_index,
                "tile_s3_key": tile.tile_s3_key,
                "offset_x": tile.offset_x,
                "offset_y": tile.offset_y,
                "ai_backend": run.backend,
                "ai_model_variant": run.model_variant,
                "sam_prompt": run.prompt,
                "classes": run.classes or [],
                "ai_confidence": run.confidence,
                "total_tiles": run.total_tiles,
                "ortho_transform": metadata.get("transform"),
                "ortho_crs": metadata.get("crs"),
            },
            event_id=deterministic_event_id(
                "image_tile",
                run.vol_id,
                run.run_id,
                tile.tile_index,
                run.retry_count,
            ),
            correlation_id=run.run_id,
            attempt=run.retry_count,
        )

    def _plan_recovery(self):
        stale_before = datetime.now(UTC) - timedelta(minutes=10)
        ready_run_ids = []
        tile_events = []
        ortho_events = []
        with get_session() as session:
            runs = (
                session.query(AIAnalysisRun)
                .filter(
                    AIAnalysisRun.status.in_(
                        (
                            "queued",
                            "tiling",
                            "running",
                            "failed",
                            "finalizing",
                        )
                    ),
                    AIAnalysisRun.heartbeat_at < stale_before,
                )
                .with_for_update(skip_locked=True)
                .limit(10)
                .all()
            )
            for run in runs:
                if run.phase == "tile_attempts_exhausted":
                    continue
                tiles = (
                    session.query(AIAnalysisTile)
                    .filter(AIAnalysisTile.analysis_run_id == run.id)
                    .order_by(AIAnalysisTile.tile_index)
                    .all()
                )
                completed = sum(tile.status == "completed" for tile in tiles)
                if run.total_tiles and completed >= run.total_tiles:
                    run.status = "finalizing"
                    run.phase = "recovery_finalizing"
                    ready_run_ids.append(run.run_id)
                elif not tiles:
                    run.retry_count += 1
                    run.status = "queued"
                    run.phase = "recovery_retiling"
                    ortho_events.append(self._orthomosaic_recovery_event(run))
                else:
                    incomplete_tiles = [item for item in tiles if item.status != "completed"]
                    exhausted_tiles = [item for item in incomplete_tiles if item.attempts >= self.maximum_tile_attempts]
                    if exhausted_tiles:
                        for tile in exhausted_tiles:
                            tile.status = "dead"
                            tile.last_error = f"Maximum AI tile attempts exhausted ({self.maximum_tile_attempts})"
                        run.status = "failed"
                        run.phase = "tile_attempts_exhausted"
                        run.error_message = (
                            f"{len(exhausted_tiles)} AI tile(s) exhausted "
                            f"the {self.maximum_tile_attempts}-attempt budget; "
                            "manual retry is required"
                        )
                        run.heartbeat_at = datetime.now(UTC)
                        continue
                    for tile in incomplete_tiles[:100]:
                        tile.attempts += 1
                        tile.status = "queued"
                        tile.last_error = None
                        tile_events.append(self._tile_recovery_event(run, tile))
                    run.status = "running"
                    run.phase = "recovery_detecting"
                run.heartbeat_at = datetime.now(UTC)
        return ready_run_ids, ortho_events, tile_events

    def recover(self):
        ready_run_ids, ortho_events, tile_events = self._plan_recovery()
        for event in ortho_events:
            self.producer.produce(
                self.orthomosaic_topic,
                key=event["vol_id"],
                value=json.dumps(event),
            )
        for event in tile_events:
            self.producer.produce(
                self.tile_topic,
                key=f"{event['vol_id']}_{event['tile_index']}",
                value=json.dumps(event),
            )
        if (ortho_events or tile_events) and self.producer.flush():
            raise RuntimeError("analysis recovery events were not delivered")
        for run_id in ready_run_ids:
            try:
                self.finalize(run_id)
            except Exception as error:
                self.logger.exception("Failed to recover AI analysis %s", run_id)
                self._mark_finalization_failed(run_id, error)
