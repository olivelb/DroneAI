"""Durable analysis attempts executed by the existing detection Stage Jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

from geoalchemy2.elements import WKTElement
from sqlalchemy import func

from shared.database import AIAnalysisRun, DetectionShardReceipt, MapFeature, MissionStageRun
from shared.detection_sharding import parse_detection_shard_plan_descriptor
from shared.phase_dag import stage_idempotency_key
from shared.stage_contracts import STAGE_DAG_VERSION, resource_class_for_stage

ACTIVE_STAGE_STATUSES = ("blocked", "queued", "running")


def linked_analysis(session: Any, stage: Any) -> Any | None:
    if getattr(stage, "analysis_run_id", None) is None:
        return None
    analysis = session.query(AIAnalysisRun).filter(
        AIAnalysisRun.id == stage.analysis_run_id,
        AIAnalysisRun.mission_id == stage.mission_id,
    ).with_for_update().one()
    if stage.stage != "detection":
        raise ValueError("An analysis must use the detection stage")
    return analysis


def current_analysis_attempt(analysis: Any, stage: Any) -> bool:
    return bool((stage.parameters or {}).get("analysis_generation") == analysis.retry_count)


def create_analysis_stage(session: Any, mission: Any, analysis: Any, raster: Any) -> Any:
    """Caller holds the mission lock, serializing stage attempt allocation."""
    if raster.mission_id != mission.id or raster.kind != "raster_product_workspace":
        raise ValueError("Analysis requires a raster artifact from the same mission")
    if raster.stage_run.stage != "rasterization" or raster.stage_run.status != "succeeded":
        raise ValueError("Analysis requires a successfully published raster stage")
    session.flush()
    attempt = int(session.query(func.max(MissionStageRun.attempt)).filter(
        MissionStageRun.mission_id == mission.id,
        MissionStageRun.stage == "detection",
    ).scalar() or 0) + 1
    parameters = {
        "dag_version": STAGE_DAG_VERSION,
        "work_drive": (mission.params or {}).get("work_drive"),
        "analysis_generation": int(analysis.retry_count or 0),
        "ai": {
            "backend": analysis.backend, "model_variant": analysis.model_variant,
            "classes": analysis.classes, "confidence": analysis.confidence,
            "confidence_policy": "strict",
            "sam_prompt": analysis.prompt, "tile_size": analysis.tile_size,
        },
    }
    upstream = [raster.artifact_id]
    stage = MissionStageRun(
        mission_id=mission.id, analysis_run_id=analysis.id,
        stage="detection", attempt=attempt, status="queued", current_step="QUEUED",
        parameters=parameters, upstream_artifact_ids=upstream,
        resource_class=resource_class_for_stage("detection", parameters),
        idempotency_key=stage_idempotency_key(
            mission.vol_id, "detection", attempt, parameters, upstream,
            organization_id=mission.organization_id,
        ),
    )
    session.add(stage)
    analysis.status = "queued"
    analysis.phase = "queued"
    analysis.progress = 0
    analysis.total_tiles = 0
    analysis.tiles_completed = 0
    analysis.detection_count = 0
    analysis.error_message = None
    analysis.result_s3_key = None
    analysis.started_at = None
    analysis.completed_at = None
    analysis.heartbeat_at = datetime.now(UTC)
    session.flush()
    return stage


def sync_analysis_stage(session: Any, stage: Any) -> None:
    """Project one stage transition into the existing operator-facing read model."""
    analysis = linked_analysis(session, stage)
    if analysis is None or not current_analysis_attempt(analysis, stage):
        return
    status = str(stage.status)
    descriptor = (stage.provenance or {}).get("detection_shard_plan")
    if status in {"queued", "running"} and descriptor is not None:
        plan = parse_detection_shard_plan_descriptor(descriptor)
        completed = int(session.query(func.sum(DetectionShardReceipt.tile_count)).filter(
            DetectionShardReceipt.stage_run_id == stage.id,
            DetectionShardReceipt.plan_checksum_sha256 == plan.checksum_sha256,
        ).scalar() or 0)
        stage.quality_metrics = {
            **(stage.quality_metrics or {}), "tile_count": plan.tile_count,
            "tiles_completed": min(completed, plan.tile_count),
        }
        stage.progress = min(99, round(100 * completed / plan.tile_count))
    analysis.status = {"succeeded": "completed", "blocked": "queued"}.get(status, status)
    analysis.phase = {
        "succeeded": "completed", "failed": "finalization_failed", "cancelled": "cancelled",
    }.get(status, "detecting" if status == "running" else "queued")
    analysis.progress = int(stage.progress or 0)
    analysis.error_message = stage.error_message if status == "failed" else None
    analysis.heartbeat_at = stage.heartbeat_at or datetime.now(UTC)
    analysis.started_at = stage.started_at
    analysis.completed_at = stage.completed_at
    metrics = stage.quality_metrics or {}
    analysis.total_tiles = int(metrics.get("tile_count", analysis.total_tiles or 0))
    analysis.tiles_completed = int(metrics.get("tiles_completed", analysis.tiles_completed or 0))
    if status == "succeeded":
        analysis.tiles_completed = analysis.total_tiles
        analysis.detection_count = int(metrics.get("geolocated_feature_count", 0))


def cancel_analysis_stages(session: Any, analysis: Any) -> bool:
    """Cooperative cancellation plus scheduler-driven Job deletion; no Kafka."""
    stages = session.query(MissionStageRun).filter(
        MissionStageRun.analysis_run_id == analysis.id,
        MissionStageRun.mission_id == analysis.mission_id,
    ).order_by(MissionStageRun.attempt.desc()).with_for_update().all()
    if not stages:
        raise ValueError("Analysis has no bounded stage attempt")
    current = stages[0]
    if current.status == "succeeded":
        return False
    for stage in stages:
        if stage.status in ACTIVE_STAGE_STATUSES:
            stage.status = "cancelled"
            stage.current_step = "CANCELLED"
            stage.completed_at = datetime.now(UTC)
            stage.heartbeat_at = stage.completed_at
            sync_analysis_stage(session, stage)
    sync_analysis_stage(session, current)
    return True


def publish_analysis_features(
    session: Any, stage: Any, metadata: dict[str, Any],
    features: tuple[dict[str, Any], ...] | None,
) -> None:
    """Publish editable features in the same transaction as the immutable result."""
    analysis = linked_analysis(session, stage)
    if analysis is None:
        if features is not None:
            raise ValueError("Pipeline detection cannot publish standalone analysis features")
        return
    if not current_analysis_attempt(analysis, stage):
        raise ValueError("Analysis generation changed before publication")
    if metadata.get("analysis_run_id") != analysis.run_id or features is None:
        raise ValueError("Analysis result identity or features are missing")
    now = datetime.now(UTC)
    if analysis.persist_results:
        session.query(MapFeature).filter(
            MapFeature.analysis_run_id == analysis.id,
            MapFeature.deleted_at.is_(None),
        ).update({
            MapFeature.deleted_at: now, MapFeature.deleted_by: "system:analysis-stage",
            MapFeature.deletion_reason: "Replaced by a successful analysis attempt",
        }, synchronize_session=False)
        for index, feature in enumerate(features):
            properties = cast(dict[str, Any], feature["properties"])
            session.add(MapFeature(
                feature_id=str(uuid5(NAMESPACE_URL, f"droneai:{stage.run_id}:feature:{index}")),
                mission_id=analysis.mission_id, analysis_run_id=analysis.id,
                vol_id=analysis.vol_id, source="ai",
                geometry=feature_wkt(feature["geometry"]),
                name=analysis.name, description=analysis.description,
                color=analysis.color, tags=analysis.tags,
                class_name=properties.get("class_name"),
                confidence=properties.get("confidence"),
                properties=properties, created_by=analysis.created_by,
            ))
    analysis.result_s3_key = metadata["geojson_object_key"]
    analysis.model_manifest = metadata["model_manifest"]
    analysis.tiling_metadata = metadata["raster"]


def feature_wkt(geometry: dict[str, Any]) -> WKTElement:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Point":
        point = cast(list[float], coordinates)
        return WKTElement(f"POINT({point[0]} {point[1]})", srid=4326)
    if geometry_type == "Polygon":
        polygon = cast(list[list[list[float]]], coordinates)
        rings = [
            "(" + ", ".join(f"{point[0]} {point[1]}" for point in ring) + ")"
            for ring in polygon
        ]
        return WKTElement(f"POLYGON({', '.join(rings)})", srid=4326)
    if geometry_type == "MultiPolygon":
        polygons = cast(list[list[list[list[float]]]], coordinates)
        bodies = []
        for polygon in polygons:
            rings = ["(" + ", ".join(f"{point[0]} {point[1]}" for point in ring) + ")" for ring in polygon]
            bodies.append("(" + ", ".join(rings) + ")")
        return WKTElement(f"MULTIPOLYGON({', '.join(bodies)})", srid=4326)
    raise ValueError(f"Unsupported AI geometry: {geometry_type}")

