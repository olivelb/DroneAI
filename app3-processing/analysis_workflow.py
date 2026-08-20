"""Durable AI campaign aggregation and recovery.

This module owns campaign state transitions and object-store publication. The
Kafka worker only dispatches events and injects its producer/topics.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import func

import analysis_publication as publication
import analysis_recovery as recovery
from analysis_publication import (
    DetectionRecord,
    JsonObject,
    RunDescriptor,
    TileResultReference,
)
from shared import storage
from shared.database import (
    AIAnalysisRun,
    AIAnalysisTile,
    Mission,
    get_session,
)
from shared.json_io import atomic_write_json
from shared.kafka_partitioning import tile_work_key
from shared.model_provenance import validate_model_manifest
from shared.tile_results import (
    build_tile_result_artifact,
    tile_result_s3_key,
    validate_tile_result_bytes,
)
from shared.tenancy import MissionObjectNamespace, mission_event_namespace


class KafkaProducer(Protocol):
    """Subset of the Kafka producer used by analysis recovery."""

    def produce(self, topic: str, *, key: str, value: str) -> None: ...

    def flush(self) -> int: ...


class AnalysisWorkflow:
    """Idempotent campaign service used by the processing worker."""

    def __init__(
        self,
        *,
        producer: KafkaProducer,
        orthomosaic_topic: str,
        tile_topic: str,
        dedupe: Callable[[list[DetectionRecord]], list[DetectionRecord]],
        logger: logging.Logger,
        maximum_tile_attempts: int | None = None,
        finalization_lease_seconds: int | None = None,
        finalization_owner: str | None = None,
        maximum_tile_result_bytes: int | None = None,
        maximum_aggregate_result_bytes: int | None = None,
        maximum_raw_detections: int | None = None,
        maximum_final_detections: int | None = None,
    ) -> None:
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
        self.finalization_heartbeat_seconds = max(
            10,
            min(300, self.finalization_lease_seconds // 3),
        )
        self._next_finalization_heartbeat = 0.0
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
    def _styled_collection(
        detections: Iterable[DetectionRecord],
        *,
        vol_id: str,
        run: Any,
        tile_index: int | None = None,
    ) -> JsonObject:
        return publication.styled_collection(
            detections,
            vol_id=vol_id,
            run=run,
            tile_index=tile_index,
        )

    @staticmethod
    def _workspace(vol_id: str, run_id: str) -> Path:
        return publication.workspace(vol_id, run_id)

    @staticmethod
    def _write_verified_json(
        payload: JsonObject,
        key: str,
        local_path: str | Path,
    ) -> None:
        publication.write_verified_json(payload, key, local_path)

    @staticmethod
    def _feature_wkt(geometry: JsonObject) -> Any:
        return publication.feature_wkt(geometry)

    @staticmethod
    def _run_descriptor(
        run: Any,
        namespace: MissionObjectNamespace | None = None,
    ) -> RunDescriptor:
        return publication.run_descriptor(run, namespace)

    @staticmethod
    def _descriptor_proxy(descriptor: RunDescriptor) -> Any:
        return publication.descriptor_proxy(descriptor)

    def _read_tile_payload(
        self,
        tile_key: str,
        total_payload_bytes: int,
        expected_size: int,
    ) -> tuple[bytes, int]:
        return publication.read_tile_payload(
            tile_key,
            total_payload_bytes,
            expected_size,
            maximum_tile_result_bytes=self.maximum_tile_result_bytes,
            maximum_aggregate_result_bytes=self.maximum_aggregate_result_bytes,
        )

    def _load_tile_payloads(
        self,
        references: Iterable[TileResultReference],
        descriptor: RunDescriptor,
        *,
        renew_finalization: bool = False,
    ) -> list[DetectionRecord]:
        return publication.load_tile_payloads(
            references,
            descriptor,
            maximum_tile_result_bytes=self.maximum_tile_result_bytes,
            maximum_aggregate_result_bytes=self.maximum_aggregate_result_bytes,
            maximum_raw_detections=self.maximum_raw_detections,
            renew_finalization=(
                self._require_finalization_ownership
                if renew_finalization
                else None
            ),
        )

    def _replace_persisted_features(
        self,
        session: Any,
        run: Any,
        collection: JsonObject,
    ) -> None:
        publication.replace_persisted_features(session, run, collection)

    @staticmethod
    def _lease_is_active(run: Any, now: datetime) -> bool:
        lease_until: datetime | None = run.finalization_lease_until
        if lease_until is None:
            return False
        if lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=UTC)
        return lease_until > now

    def _claim_finalization(
        self,
        run_id: str,
    ) -> tuple[RunDescriptor, list[TileResultReference]] | None:
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
            self._next_finalization_heartbeat = (
                time.monotonic() + self.finalization_heartbeat_seconds
            )
            mission = session.query(Mission).filter(
                Mission.id == run.mission_id
            ).one()
            namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                mission.vol_id,
                mission.workspace_prefix,
            )
            descriptor = self._run_descriptor(run, namespace)
            references: list[TileResultReference] = []
            for tile in tiles:
                if (
                    tile.result_s3_key is None
                    or tile.result_sha256 is None
                    or tile.result_size_bytes is None
                    or tile.result_attempt is None
                ):
                    raise RuntimeError(
                        f"AI tile {tile.tile_index} is missing result integrity metadata"
                    )
                references.append(
                    {
                        "key": tile.result_s3_key,
                        "sha256": tile.result_sha256,
                        "size_bytes": int(tile.result_size_bytes),
                        "tile_index": int(tile.tile_index),
                        "attempt": int(tile.result_attempt),
                        "detection_count": int(tile.detection_count),
                    }
                )
            return descriptor, references

    def _renew_finalization_lease(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> bool:
        monotonic_now = time.monotonic()
        if not force and monotonic_now < self._next_finalization_heartbeat:
            return True
        now = datetime.now(UTC)
        with get_session() as session:
            run = (
                session.query(AIAnalysisRun)
                .filter(AIAnalysisRun.run_id == run_id)
                .with_for_update()
                .first()
            )
            if (
                run is None
                or run.status != "finalizing"
                or run.finalization_owner != self.finalization_owner
            ):
                return False
            run.finalization_lease_until = now + timedelta(
                seconds=self.finalization_lease_seconds
            )
            run.heartbeat_at = now
        self._next_finalization_heartbeat = (
            monotonic_now + self.finalization_heartbeat_seconds
        )
        return True

    def _require_finalization_ownership(
        self,
        run_id: str,
        *,
        force: bool = False,
    ) -> None:
        if not self._renew_finalization_lease(run_id, force=force):
            raise RuntimeError(
                f"AI analysis finalization lease was lost for {run_id}"
            )

    def finalize(self, run_id: str) -> bool:
        claim = self._claim_finalization(run_id)
        if claim is None:
            return False
        descriptor, references = claim

        raw = self._load_tile_payloads(
            references,
            descriptor,
            renew_finalization=True,
        )
        self._require_finalization_ownership(run_id, force=True)
        unique = self.dedupe(raw)
        self._require_finalization_ownership(run_id, force=True)
        if len(unique) > self.maximum_final_detections:
            raise RuntimeError(
                f"AI analysis exceeds the final detection safety limit ({self.maximum_final_detections})"
            )
        collection = self._styled_collection(
            unique,
            vol_id=descriptor["vol_id"],
            run=self._descriptor_proxy(descriptor),
        )
        namespace = MissionObjectNamespace.from_binding(
            cast(str, descriptor["organization_id"]),
            cast(str, descriptor["vol_id"]),
            cast(str, descriptor["workspace_prefix"]),
        )
        result_key = namespace.key(
            "analyses",
            cast(str, descriptor["run_id"]),
            "detections.geojson",
        )
        self._require_finalization_ownership(run_id, force=True)
        self._write_verified_json(
            collection,
            result_key,
            str(
                self._workspace(
                    descriptor["vol_id"],
                    descriptor["run_id"],
                )
                / "detections.geojson"
            ),
        )
        self._require_finalization_ownership(run_id, force=True)

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
            run.tiles_completed = len(references)
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
    def _get_tile_context(
        session: Any,
        vol_id: str,
        run_id: str,
        tile_index: int,
    ) -> tuple[Any, Any]:
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
    def _resume_finalization_if_ready(
        session: Any,
        run: Any,
        receipt: Any,
    ) -> bool:
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

    def _stage_tile_result(
        self,
        data: JsonObject,
        run: Any,
    ) -> tuple[str, int, str, int]:
        vol_id = cast(str, data["vol_id"])
        run_id = cast(str, data["analysis_run_id"])
        tile_index = int(data["tile_index"])
        event_attempt = int(data.get("attempt", 0))
        model_manifest = cast(JsonObject, run.model_manifest)
        namespace = mission_event_namespace(data)
        result_key = tile_result_s3_key(
            vol_id,
            run_id,
            tile_index,
            event_attempt,
            organization_id=namespace.organization_id,
            workspace_prefix=namespace.root,
        )
        referenced_key = data.get("result_s3_key")
        if referenced_key is not None:
            if referenced_key != result_key:
                raise RuntimeError(
                    "AI tile result key does not match the deterministic mission/run key"
                )
            result_sha256 = cast(str, data["result_sha256"])
            result_size = int(data["result_size_bytes"])
            detection_count = int(data["detection_count"])
            raw_payload, _ = self._read_tile_payload(
                result_key,
                0,
                result_size,
            )
            validate_tile_result_bytes(
                raw_payload,
                expected_sha256=result_sha256,
                expected_size=result_size,
                vol_id=vol_id,
                analysis_run_id=run_id,
                tile_index=tile_index,
                attempt=event_attempt,
                detection_count=detection_count,
                model_manifest=model_manifest,
            )
            return result_key, detection_count, result_sha256, result_size

        detections = cast(list[DetectionRecord], data.get("detections") or [])
        artifact = build_tile_result_artifact(
            vol_id=vol_id,
            analysis_run_id=run_id,
            tile_index=tile_index,
            attempt=event_attempt,
            model_manifest=model_manifest,
            detections=detections,
        )
        local_path = (
            self._workspace(vol_id, run_id)
            / "results"
            / f"tile_{tile_index}.json"
        )
        atomic_write_json(local_path, artifact)
        try:
            uploaded = storage.upload_verified_file(local_path, result_key)
        finally:
            local_path.unlink(missing_ok=True)
        return (
            result_key,
            len(detections),
            str(uploaded["sha256"]),
            int(uploaded["size"]),
        )

    @staticmethod
    def _mark_tile_complete(
        run_id: str,
        tile_index: int,
        result_key: str,
        count: int,
        result_sha256: str,
        result_size_bytes: int,
        expected_attempt: int,
    ) -> bool:
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
                receipt.result_sha256 = result_sha256
                receipt.result_size_bytes = result_size_bytes
                receipt.result_attempt = expected_attempt
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

    def _mark_finalization_failed(self, run_id: str, error: Exception) -> None:
        with get_session() as session:
            run = session.query(AIAnalysisRun).filter(AIAnalysisRun.run_id == run_id).with_for_update().first()
            if run is not None and run.finalization_owner in {
                None,
                self.finalization_owner,
            } and run.status != "cancelled":
                run.status = "failed"
                run.phase = "finalization_failed"
                run.error_message = str(error)
                run.heartbeat_at = datetime.now(UTC)
                run.finalization_owner = None
                run.finalization_lease_until = None

    def process_detection(self, data: JsonObject) -> None:
        vol_id = cast(str, data["vol_id"])
        run_id = cast(str, data["analysis_run_id"])
        tile_index = int(data["tile_index"])
        with get_session() as session:
            run, receipt = self._get_tile_context(session, vol_id, run_id, tile_index)
            mission = session.query(Mission).filter(
                Mission.id == run.mission_id
            ).one()
            durable_namespace = MissionObjectNamespace.from_binding(
                mission.organization_id,
                mission.vol_id,
                mission.workspace_prefix,
            )
            if mission_event_namespace(data) != durable_namespace:
                raise RuntimeError(
                    "AI tile result namespace does not match the durable mission"
                )
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
        result_key, count, result_sha256, result_size_bytes = (
            self._stage_tile_result(data, run_descriptor)
        )
        if not self._mark_tile_complete(
            run_id,
            tile_index,
            result_key,
            count,
            result_sha256,
            result_size_bytes,
            event_attempt,
        ):
            return
        try:
            self.finalize(run_id)
        except Exception as error:
            self._mark_finalization_failed(run_id, error)
            raise

    @staticmethod
    def _orthomosaic_recovery_event(
        run: Any,
        namespace: MissionObjectNamespace,
    ) -> JsonObject:
        return recovery.orthomosaic_recovery_event(run, namespace)

    @staticmethod
    def _tile_recovery_event(
        run: Any,
        tile: Any,
        namespace: MissionObjectNamespace,
    ) -> JsonObject:
        return recovery.tile_recovery_event(run, tile, namespace)

    def _plan_recovery(
        self,
    ) -> tuple[list[str], list[JsonObject], list[JsonObject]]:
        return recovery.plan_recovery(
            session_factory=get_session,
            maximum_tile_attempts=self.maximum_tile_attempts,
        )

    def recover(self) -> None:
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
                key=tile_work_key(
                    event["vol_id"],
                    event.get("analysis_run_id"),
                    event["tile_index"],
                    organization_id=event.get("organization_id"),
                ),
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
