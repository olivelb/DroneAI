"""Shared query and serialization helpers for map routers."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol, Self, cast

from fastapi import HTTPException, status
from sqlalchemy import func, select

from shared import storage
from shared.config import S3_BUCKET
from shared.database import MapFeature, MissionArtifact, MissionStageRun
from shared.geospatial_workspace import bounds_intersect, geometry_bounds
from shared.stage_workspace import resolve_workspace_files
from shared.tenancy import MissionObjectNamespace

from .mission_access import get_owned_mission
from .security import Principal

VOL_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,256}$")
RASTER_LAYERS: dict[str, tuple[str, str]] = {
    "ortho": ("orthomosaic.tif", ""),
    "depth": ("orthomosaic.height.tif", "depth"),
}
MAX_VECTOR_OBJECT_BYTES = 20_000_000
Bounds = tuple[float, float, float, float]
JsonObject = dict[str, Any]


class QueryProtocol(Protocol):
    def filter(self, *criteria: Any) -> Self: ...

    def first(self) -> Any: ...


class SessionProtocol(Protocol):
    def query(self, *entities: Any) -> QueryProtocol: ...

    def scalar(self, statement: Any) -> Any: ...


class FilterQueryProtocol(Protocol):
    def filter(self, *criteria: Any) -> Self: ...


class RouteQuery(Protocol):
    """Dynamic SQLAlchemy query operations used by HTTP route adapters."""

    def filter(self, *criteria: Any) -> Self: ...

    def join(self, *targets: Any) -> Self: ...

    def outerjoin(self, *targets: Any) -> Self: ...

    def order_by(self, *criteria: Any) -> Self: ...

    def limit(self, value: int) -> Self: ...

    def first(self) -> Any: ...

    def all(self) -> list[Any]: ...

    def with_for_update(self) -> Self: ...

    def yield_per(self, value: int) -> Self: ...

    def update(
        self,
        values: dict[Any, Any],
        *,
        synchronize_session: bool,
    ) -> int: ...


class RouteSession(Protocol):
    """Narrow session boundary shared by typed map route adapters."""

    def query(self, *entities: Any) -> RouteQuery: ...

    def scalar(self, statement: Any) -> Any: ...

    def add(self, instance: Any) -> None: ...

    def delete(self, instance: Any) -> None: ...

    def expire(self, instance: Any, attribute_names: list[str] | None = None) -> None: ...

    def flush(self) -> None: ...


class MissionRecord(Protocol):
    id: int
    vol_id: str
    organization_id: str
    owner_subject: str
    workspace_prefix: str | None
    input_dataset: str | None
    tiling_metadata: JsonObject | None
    status: str
    current_step: str | None


class MissionArtifactRecord(Protocol):
    artifact_id: str
    uri: str
    checksum_sha256: str
    artifact_metadata: JsonObject


@dataclass(frozen=True)
class RasterProductObject:
    key: str
    default_colormap: str
    sidecar_key: str | None = None
    artifact_id: str | None = None


@dataclass(frozen=True)
class DetectionProductObject:
    key: str
    artifact_id: str


class MapFeatureMutationRecord(Protocol):
    geometry: Any
    version: int
    updated_at: datetime


class AnalysisRunRecord(Protocol):
    id: int
    run_id: str
    mission_id: int
    vol_id: str
    name: str
    description: str | None
    color: str
    tags: list[str]
    backend: str
    model_variant: str | None
    prompt: str | None
    classes: list[str]
    confidence: float
    tile_size: int
    persist_results: bool
    status: str
    phase: str
    progress: int
    total_tiles: int
    tiles_completed: int
    detection_count: int
    retry_count: int
    error_message: str | None
    ortho_s3_key: str
    result_s3_key: str | None
    model_manifest: JsonObject | None
    tiling_metadata: JsonObject | None
    heartbeat_at: datetime
    created_at: datetime | None
    updated_at: datetime | None
    completed_at: datetime | None


def validate_map_layer(vol_id: str, layer: str) -> tuple[str, str]:
    """Validate the requested mission/layer and return its logical raster name."""
    if not VOL_ID_PATTERN.fullmatch(vol_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid mission identifier",
        )
    try:
        suffix, colormap = RASTER_LAYERS[layer]
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown raster layer",
        ) from error
    return suffix, colormap


def _artifact_manifest_key(artifact: MissionArtifactRecord) -> str:
    metadata_key = artifact.artifact_metadata.get("manifest_key")
    if isinstance(metadata_key, str) and metadata_key:
        return metadata_key
    prefix = f"s3://{S3_BUCKET}/"
    if artifact.uri.startswith(prefix):
        return artifact.uri.removeprefix(prefix)
    raise ValueError("Raster artifact has no canonical manifest key")


@lru_cache(maxsize=64)
def _workspace_object_keys(
    manifest_key: str,
    checksum_sha256: str,
    organization_id: str,
) -> dict[str, str]:
    return {
        path: entry.blob.key
        for path, entry in resolve_workspace_files(
            manifest_key,
            checksum_sha256,
            expected_organization_id=organization_id,
        ).items()
    }


def resolve_raster_product(
    session: RouteSession,
    mission: MissionRecord,
    vol_id: str,
    layer: str,
) -> RasterProductObject:
    """Resolve a map layer only from its immutable raster artifact."""

    MissionObjectNamespace.from_binding(
        mission.organization_id,
        mission.vol_id,
        mission.workspace_prefix,
    )
    logical_path, colormap = validate_map_layer(vol_id, layer)
    artifact = cast(
        MissionArtifactRecord | None,
        session.query(MissionArtifact)
        .filter(
            MissionArtifact.mission_id == mission.id,
            MissionArtifact.kind == "raster_product_workspace",
        )
        .order_by(MissionArtifact.created_at.desc(), MissionArtifact.id.desc())
        .first(),
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Raster artifact is not available",
        )

    metadata_name = "ortho_file" if layer == "ortho" else "height_file"
    configured_path = artifact.artifact_metadata.get(metadata_name)
    if isinstance(configured_path, str) and configured_path:
        logical_path = configured_path
    try:
        object_keys = _workspace_object_keys(
            _artifact_manifest_key(artifact),
            artifact.checksum_sha256,
            mission.organization_id,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to resolve raster artifact manifest: {error}",
        ) from error
    key = object_keys.get(logical_path)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Raster artifact does not publish {logical_path}",
        )
    require_object(key)
    artifact_sidecar_key = object_keys.get(f"{logical_path}.cog.json")
    if artifact_sidecar_key is not None and not storage.file_exists(artifact_sidecar_key):
        artifact_sidecar_key = None
    return RasterProductObject(
        key=key,
        default_colormap=colormap,
        sidecar_key=artifact_sidecar_key,
        artifact_id=artifact.artifact_id,
    )


def resolve_detection_product(
    session: RouteSession,
    mission: MissionRecord,
) -> DetectionProductObject | None:
    artifact = cast(
        MissionArtifactRecord | None,
        session.query(MissionArtifact)
        .join(MissionStageRun, MissionStageRun.id == MissionArtifact.stage_run_id)
        .filter(
            MissionArtifact.mission_id == mission.id,
            MissionArtifact.kind == "detection_workspace",
            MissionStageRun.analysis_run_id.is_(None),
        )
        .order_by(MissionArtifact.created_at.desc(), MissionArtifact.id.desc())
        .first(),
    )
    if artifact is None:
        return None
    logical_path = artifact.artifact_metadata.get("geojson_file")
    if not isinstance(logical_path, str) or not logical_path:
        logical_path = ".droneai/detection/detections.geojson"
    try:
        object_keys = _workspace_object_keys(
            _artifact_manifest_key(artifact),
            artifact.checksum_sha256,
            mission.organization_id,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to resolve detection artifact manifest: {error}",
        ) from error
    key = object_keys.get(logical_path)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Detection artifact does not publish {logical_path}",
        )
    require_object(key)
    return DetectionProductObject(key=key, artifact_id=artifact.artifact_id)


def require_object(key: str) -> None:
    if not storage.file_exists(key):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Map product is not available",
        )


def parse_bbox(value: str | None) -> Bounds | None:
    if not value:
        return None
    try:
        bounds = tuple(float(item) for item in value.split(","))
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="bbox must contain west,south,east,north",
        ) from error
    if (
        len(bounds) != 4
        or not all(math.isfinite(coordinate) for coordinate in bounds)
        or bounds[0] >= bounds[2]
        or bounds[1] >= bounds[3]
        or bounds[0] < -180
        or bounds[2] > 180
        or bounds[1] < -90
        or bounds[3] > 90
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid WGS84 bbox",
        )
    return bounds


def get_mission(
    session: SessionProtocol,
    vol_id: str,
    principal: Principal,
    *,
    owner_subject: str | None = None,
    action: str = "map_read",
    for_update: bool = False,
) -> MissionRecord:
    return cast(
        MissionRecord,
        get_owned_mission(
            cast(Any, session),
            vol_id,
            principal,
            requested_owner=owner_subject,
            action=action,
            for_update=for_update,
        ),
    )


def serialize_run(run: AnalysisRunRecord) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "vol_id": run.vol_id,
        "name": run.name,
        "description": run.description or "",
        "color": run.color,
        "tags": run.tags or [],
        "backend": run.backend,
        "model_variant": run.model_variant,
        "prompt": run.prompt,
        "classes": run.classes or [],
        "confidence": run.confidence,
        "tile_size": run.tile_size,
        "persist_results": bool(run.persist_results),
        "status": run.status,
        "phase": run.phase,
        "progress": run.progress,
        "total_tiles": run.total_tiles,
        "tiles_completed": run.tiles_completed,
        "detection_count": run.detection_count,
        "retry_count": run.retry_count,
        "error_message": run.error_message,
        "result_s3_key": run.result_s3_key,
        "model_manifest": run.model_manifest,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


def stored_map_feature_geojson(
    feature: MapFeature,
    geometry_json: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one stored feature consistently across API and exports."""

    reviewed_at = getattr(feature, "reviewed_at", None)
    deleted_at = getattr(feature, "deleted_at", None)
    return {
        "type": "Feature",
        "id": feature.feature_id,
        "geometry": json.loads(geometry_json),
        "properties": {
            **(feature.properties or {}),
            "feature_id": feature.feature_id,
            "source": feature.source,
            "run_id": run_id,
            "name": feature.name,
            "description": feature.description or "",
            "color": feature.color,
            "tags": feature.tags or [],
            "class_name": feature.class_name,
            "confidence": feature.confidence,
            "version": feature.version,
            "created_by": feature.created_by,
            "reviewed": reviewed_at is not None,
            "reviewed_at": reviewed_at.isoformat() if reviewed_at else None,
            "reviewed_by": getattr(feature, "reviewed_by", None),
            "deleted": deleted_at is not None,
            "deleted_at": deleted_at.isoformat() if deleted_at else None,
            "deleted_by": getattr(feature, "deleted_by", None),
            "deletion_reason": getattr(feature, "deletion_reason", None),
            "updated_at": (feature.updated_at.isoformat() if feature.updated_at else None),
        },
    }


def map_feature_geojson(
    session: SessionProtocol,
    feature: MapFeature,
) -> dict[str, Any]:
    geometry_json = cast(
        str,
        session.scalar(select(func.ST_AsGeoJSON(MapFeature.geometry)).where(MapFeature.id == feature.id)),
    )
    run_id = feature.analysis_run.run_id if feature.analysis_run is not None else None
    return stored_map_feature_geojson(feature, geometry_json, run_id)


def feature_collection(
    features: list[dict[str, Any]],
    **properties: Any,
) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "feature_count": len(features),
            **properties,
        },
    }


def apply_spatial_filter[FilterQueryT: FilterQueryProtocol](
    query: FilterQueryT,
    geometry_column: Any,
    bounds: Bounds | None,
) -> FilterQueryT:
    if not bounds:
        return query
    west, south, east, north = bounds
    return query.filter(
        func.ST_Intersects(
            geometry_column,
            func.ST_MakeEnvelope(west, south, east, north, 4326),
        )
    )


def load_json_object(key: str) -> dict[str, Any]:
    stream, content_length, _ = storage.get_object_stream(key)
    if content_length > MAX_VECTOR_OBJECT_BYTES:
        stream.close()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Vector tile is too large",
        )
    try:
        payload = json.loads(stream.read())
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Stored vector payload must be a JSON object",
            )
        return cast(JsonObject, payload)
    finally:
        stream.close()


def pipeline_detection_features(
    session: RouteSession,
    mission: MissionRecord,
    vol_id: str,
    bounds: Bounds | None,
    limit: int,
) -> tuple[list[JsonObject], bool] | None:
    """Load the authoritative immutable detection layer for a stage mission."""

    product = resolve_detection_product(session, mission)
    if product is None:
        return None
    return detection_product_features(product, vol_id, bounds, limit, {"source": "pipeline"})


def detection_product_features(
    product: DetectionProductObject, vol_id: str, bounds: Bounds | None,
    limit: int, extra_properties: JsonObject,
) -> tuple[list[JsonObject], bool]:
    payload = load_json_object(product.key)
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Detection artifact is not a GeoJSON FeatureCollection",
        )
    collection_properties = payload.get("properties")
    if isinstance(collection_properties, dict):
        recorded_vol_id = collection_properties.get("vol_id")
        if recorded_vol_id is not None and recorded_vol_id != vol_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Detection artifact mission identity does not match",
            )
    selected: list[JsonObject] = []
    truncated = False
    for raw_feature in cast(list[object], payload["features"]):
        if not isinstance(raw_feature, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Detection artifact contains an invalid feature",
            )
        feature = cast(JsonObject, raw_feature)
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or not isinstance(properties, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Detection artifact contains an incomplete feature",
            )
        feature_vol_id = properties.get("vol_id")
        if feature_vol_id is not None and feature_vol_id != vol_id:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Detection feature mission identity does not match",
            )
        if bounds:
            try:
                feature_bounds = geometry_bounds(cast(JsonObject, geometry))
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Detection artifact geometry is invalid: {error}",
                ) from error
            if not bounds_intersect(list(bounds), feature_bounds):
                continue
        if len(selected) >= limit:
            truncated = True
            break
        selected.append(
            {
                **feature,
                "properties": {
                    **cast(JsonObject, properties),
                    **extra_properties,
                    "name": properties.get("name") or properties.get("class_name"),
                    "color": properties.get("color") or "#f43f5e",
                },
            }
        )
    return selected, truncated


def analysis_artifact_features(
    session: RouteSession, mission: MissionRecord, run: AnalysisRunRecord,
    bounds: Bounds | None, limit: int,
) -> tuple[list[JsonObject], bool]:
    """Read this analysis's immutable result, never a pipeline artifact."""
    if run.status != "completed":
        return [], False
    artifact = session.query(MissionArtifact).join(
        MissionStageRun, MissionStageRun.id == MissionArtifact.stage_run_id,
    ).filter(
        MissionArtifact.mission_id == mission.id,
        MissionArtifact.kind == "detection_workspace",
        MissionStageRun.analysis_run_id == run.id,
        MissionStageRun.status == "succeeded",
    ).order_by(MissionStageRun.attempt.desc()).first()
    if artifact is None:
        raise HTTPException(status_code=404, detail="Analysis artifact is not available")
    metadata = artifact.artifact_metadata or {}
    if metadata.get("analysis_run_id") != run.run_id:
        raise HTTPException(status_code=502, detail="Analysis artifact identity does not match")
    try:
        object_keys = _workspace_object_keys(
            _artifact_manifest_key(artifact), artifact.checksum_sha256, mission.organization_id,
        )
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=502, detail=f"Unable to resolve analysis artifact manifest: {error}") from error
    logical_path = metadata.get("geojson_file")
    key = object_keys.get(logical_path) if isinstance(logical_path, str) else None
    if key is None:
        raise HTTPException(status_code=502, detail="Analysis artifact has no GeoJSON result")
    return detection_product_features(
        DetectionProductObject(key=key, artifact_id=artifact.artifact_id),
        mission.vol_id, bounds, limit,
        {"source": "ai", "run_id": run.run_id, "name": run.name, "color": run.color},
    )
