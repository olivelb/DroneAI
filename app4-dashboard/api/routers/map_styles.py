"""Owner-scoped named raster display styles."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from shared.database import MissionArtifact, RasterLayerStyle, get_session

from ..map_schemas import RasterStyleCreate, RasterStyleUpdate
from ..map_support import JsonObject, MissionRecord, RouteSession, get_mission, mission_key
from ..security import Principal, require_authenticated, require_operator

router = APIRouter()


@contextmanager
def _style_context(
    vol_id: str,
    layer: str,
    principal: Principal,
    owner_subject: str | None,
    action: str,
) -> Iterator[tuple[RouteSession, MissionRecord]]:
    mission_key(vol_id, layer)
    with get_session() as session:
        typed_session = cast(RouteSession, session)
        mission = get_mission(
            typed_session,
            vol_id,
            principal,
            owner_subject=owner_subject,
            action=action,
        )
        yield typed_session, mission


def _serialize_style(style: RasterLayerStyle) -> JsonObject:
    return {
        "style_id": style.style_id,
        "layer": style.layer_key,
        "name": style.name,
        "artifact_id": (
            style.artifact.artifact_id
            if getattr(style, "artifact", None) is not None
            else None
        ),
        "style": style.style or {},
        "is_default": style.is_default,
        "version": style.version,
        "created_by": style.created_by,
        "updated_by": style.updated_by,
        "created_at": style.created_at.isoformat() if style.created_at else None,
        "updated_at": style.updated_at.isoformat() if style.updated_at else None,
    }


def _artifact_database_id(
    session: RouteSession,
    mission_id: int,
    artifact_id: str | None,
) -> int | None:
    if artifact_id is None:
        return None
    artifact = session.query(MissionArtifact).filter(
        MissionArtifact.mission_id == mission_id,
        MissionArtifact.artifact_id == artifact_id,
    ).first()
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Raster artifact does not belong to this mission",
        )
    return cast(int, artifact.id)


def _clear_default(
    session: RouteSession,
    mission_id: int,
    layer: str,
    *,
    except_id: int | None = None,
) -> None:
    query = session.query(RasterLayerStyle).filter(
        RasterLayerStyle.mission_id == mission_id,
        RasterLayerStyle.layer_key == layer,
        RasterLayerStyle.is_default.is_(True),
    )
    if except_id is not None:
        query = query.filter(RasterLayerStyle.id != except_id)
    query.update({RasterLayerStyle.is_default: False}, synchronize_session=False)


@router.get("/{vol_id}/styles/{layer}")
def list_raster_styles(
    vol_id: str,
    layer: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    with _style_context(
        vol_id, layer, principal, owner_subject, "raster_style_list"
    ) as (typed_session, mission):
        styles = cast(
            list[RasterLayerStyle],
            typed_session.query(RasterLayerStyle)
            .filter(
                RasterLayerStyle.mission_id == mission.id,
                RasterLayerStyle.layer_key == layer,
            )
            .order_by(RasterLayerStyle.is_default.desc(), RasterLayerStyle.name)
            .all(),
        )
        return {"layer": layer, "styles": [_serialize_style(style) for style in styles]}


@router.post("/{vol_id}/styles/{layer}", status_code=status.HTTP_201_CREATED)
def create_raster_style(
    vol_id: str,
    layer: str,
    request: RasterStyleCreate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    try:
        with _style_context(
            vol_id, layer, principal, owner_subject, "raster_style_create"
        ) as (typed_session, mission):
            if request.is_default:
                _clear_default(typed_session, mission.id, layer)
            style = RasterLayerStyle(
                mission_id=mission.id,
                artifact_id=_artifact_database_id(
                    typed_session, mission.id, request.artifact_id
                ),
                layer_key=layer,
                name=request.name,
                style=request.style.model_dump(mode="json"),
                is_default=request.is_default,
                created_by=principal.subject,
                updated_by=principal.subject,
            )
            typed_session.add(style)
            typed_session.flush()
            return _serialize_style(style)
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A style with this name already exists for the layer",
        ) from error


@router.patch("/{vol_id}/styles/{layer}/{style_id}")
def update_raster_style(
    vol_id: str,
    layer: str,
    style_id: str,
    request: RasterStyleUpdate,
    principal: Annotated[Principal, Depends(require_operator)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> JsonObject:
    try:
        with _style_context(
            vol_id, layer, principal, owner_subject, "raster_style_update"
        ) as (typed_session, mission):
            style = typed_session.query(RasterLayerStyle).filter(
                RasterLayerStyle.mission_id == mission.id,
                RasterLayerStyle.layer_key == layer,
                RasterLayerStyle.style_id == style_id,
            ).with_for_update().first()
            if style is None:
                raise HTTPException(status_code=404, detail="Raster style not found")
            if int(style.version) != request.version:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Raster style was changed by another user",
                        "current_version": style.version,
                    },
                )
            changes = request.model_dump(exclude_unset=True, exclude={"version"})
            if changes.get("is_default") is True:
                _clear_default(typed_session, mission.id, layer, except_id=style.id)
            record = cast(Any, style)
            if "style" in changes and request.style is not None:
                record.style = request.style.model_dump(mode="json")
            if "name" in changes and request.name is not None:
                record.name = request.name
            if "is_default" in changes and request.is_default is not None:
                record.is_default = request.is_default
            record.updated_by = principal.subject
            record.version += 1
            typed_session.flush()
            return _serialize_style(cast(RasterLayerStyle, style))
    except IntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A style with this name already exists for the layer",
        ) from error
