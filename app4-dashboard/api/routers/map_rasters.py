"""COG metadata/tiles and the combined legacy/manual vector layer."""

from __future__ import annotations

import json
import math
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from shared import storage
from shared.database import Detection, MapFeature, get_session
from shared.geospatial_assets import (
    detections_feature_collection,
    inspect_remote_cog,
    render_cog_tile,
)

from ..map_support import (
    Bounds,
    JsonObject,
    MissionRecord,
    RouteSession,
    apply_detection_spatial_filter,
    apply_spatial_filter,
    feature_collection,
    get_mission,
    map_feature_geojson,
    mission_key,
    parse_bbox,
    require_object,
)
from ..map_schemas import RasterPalette
from ..raster_style_contract import parse_band_indexes, parse_display_ranges
from ..security import Principal, require_authenticated

router = APIRouter()


@router.get("/{vol_id}/metadata/{layer}")
def raster_layer_metadata(
    vol_id: str,
    layer: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with get_session() as session:
        get_mission(
            cast(RouteSession, session),
            vol_id,
            principal,
            owner_subject=owner_subject,
        )
    key, _ = mission_key(vol_id, layer)
    require_object(key)
    sidecar_key = f"{key}.cog.json"
    if storage.file_exists(sidecar_key):
        stream, content_length, _ = storage.get_object_stream(sidecar_key)
        if content_length > 1_000_000:
            stream.close()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Invalid COG metadata sidecar",
            )
        try:
            # Older height-map sidecars may contain the non-standard JSON
            # token NaN for their IEEE floating-point NoData value.
            payload = cast(
                JsonObject,
                json.loads(
                    stream.read(),
                    parse_constant=lambda _constant: None,
                ),
            )
        finally:
            stream.close()
        payload["s3_key"] = key
        if "display_ranges" not in payload:
            try:
                inspected = inspect_remote_cog(
                    storage.get_presigned_url(key, public=False),
                    s3_key=key,
                )
                payload["display_ranges"] = inspected.get("display_ranges")
            except Exception:
                # Legacy sidecars remain usable; only their display falls back
                # to the historical per-tile normalization.
                pass
        return payload
    try:
        return inspect_remote_cog(
            storage.get_presigned_url(key, public=False),
            s3_key=key,
        )
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unable to inspect COG: {error}",
        ) from error


@router.get("/{vol_id}/tiles/{layer}/{z}/{x}/{y}.png")
def raster_tile(
    vol_id: str,
    layer: str,
    z: int,
    x: int,
    y: int,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    display_min: float | None = Query(default=None),
    display_max: float | None = Query(default=None),
    bands: str | None = Query(default=None, max_length=32),
    display_ranges: str | None = Query(default=None, max_length=512),
    palette: Annotated[RasterPalette | None, Query()] = None,
) -> StreamingResponse:
    with get_session() as session:
        get_mission(
            cast(RouteSession, session),
            vol_id,
            principal,
            owner_subject=owner_subject,
        )
    key, default_colormap = mission_key(vol_id, layer)
    require_object(key)
    if (display_min is None) != (display_max is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="display_min and display_max must be provided together",
        )
    if display_ranges is not None and display_min is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Use display_ranges or display_min/display_max, not both",
        )
    if (
        display_min is not None
        and display_max is not None
        and (
            not math.isfinite(display_min)
            or not math.isfinite(display_max)
            or display_max <= display_min
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="display_max must be greater than display_min",
        )
    legacy_display_ranges = (
        [[display_min, display_max]]
        if display_min is not None and display_max is not None
        else None
    )
    try:
        band_indexes = parse_band_indexes(bands)
        configured_ranges = parse_display_ranges(
            display_ranges,
            expected_count=len(band_indexes) if band_indexes else None,
        )
        output = render_cog_tile(
            storage.get_presigned_url(key, expires=900, public=False),
            z=z,
            x=x,
            y=y,
            colormap=(
                default_colormap
                if palette is None
                else "" if palette in {"none", "gray"} else palette
            ),
            display_ranges=configured_ranges or legacy_display_ranges,
            band_indexes=band_indexes,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"COG tile rendering failed: {error}",
        ) from error
    return StreamingResponse(
        output,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


def _legacy_features(
    session: RouteSession,
    mission: MissionRecord,
    vol_id: str,
    bounds: Bounds | None,
    limit: int,
) -> tuple[list[JsonObject], bool]:
    query = session.query(Detection).filter(Detection.vol_id == vol_id)
    query = apply_detection_spatial_filter(query, bounds)
    records = query.order_by(Detection.id).limit(limit + 1).all()
    metadata = mission.tiling_metadata or {}
    payload = detections_feature_collection(
        records[:limit],
        geotransform=metadata.get("transform"),
        source_crs=metadata.get("crs"),
        vol_id=vol_id,
    )
    features = cast(list[JsonObject], payload["features"])
    for feature in features:
        properties = cast(JsonObject, feature["properties"])
        properties.update({"source": "legacy", "color": "#f43f5e"})
    return features, len(records) > limit


def _stored_features(
    session: RouteSession,
    vol_id: str,
    bounds: Bounds | None,
    requested_sources: set[str],
    requested_runs: set[str],
    limit: int,
) -> tuple[list[JsonObject], bool]:
    query = session.query(MapFeature).filter(
        MapFeature.vol_id == vol_id,
        MapFeature.source.in_(requested_sources),
        MapFeature.deleted_at.is_(None),
    )
    if requested_runs:
        from shared.database import AIAnalysisRun

        query = query.join(AIAnalysisRun).filter(
            AIAnalysisRun.run_id.in_(requested_runs)
        )
    query = apply_spatial_filter(query, MapFeature.geometry, bounds)
    records = cast(
        list[MapFeature],
        query.order_by(MapFeature.id).limit(limit + 1).all(),
    )
    return (
        [map_feature_geojson(session, record) for record in records[:limit]],
        len(records) > limit,
    )


@router.get("/{vol_id}/vectors.geojson")
def vector_layer(
    vol_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
    bbox: str | None = Query(default=None),
    sources: str = Query(default="legacy,manual,ai"),
    run_ids: str | None = Query(default=None),
    limit: int = Query(default=10_000, ge=1, le=50_000),
) -> JsonObject:
    mission_key(vol_id, "ortho")
    bounds = parse_bbox(bbox)
    requested_sources = {
        value.strip() for value in sources.split(",") if value.strip()
    }
    requested_runs = {
        value.strip() for value in (run_ids or "").split(",") if value.strip()
    }
    features: list[dict[str, Any]] = []
    truncated = False
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
        )
        if "legacy" in requested_sources:
            legacy, truncated = _legacy_features(
                typed_session, mission, vol_id, bounds, limit
            )
            features.extend(legacy)
        remaining = max(0, limit - len(features))
        if remaining and requested_sources.intersection({"manual", "ai"}):
            stored, stored_truncated = _stored_features(
                typed_session,
                vol_id,
                bounds,
                requested_sources,
                requested_runs,
                remaining,
            )
            features.extend(stored)
            truncated = truncated or stored_truncated
    return feature_collection(features, vol_id=vol_id, truncated=truncated)
