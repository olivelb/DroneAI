"""Authorized Gaussian viewer discovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from shared.database import get_session

from ..gaussian_viewer import (
    VIEWER_DESCRIPTOR_CACHE_SECONDS,
    gaussian_viewer_descriptor,
)
from ..mission_access import get_owned_mission
from ..security import Principal, require_authenticated

router = APIRouter()


@router.get("/missions/{vol_id}/gaussians/viewer")
def mission_gaussian_viewer(
    vol_id: str,
    response: Response,
    principal: Annotated[Principal, Depends(require_authenticated)],
    owner_subject: Annotated[str | None, Query(max_length=256)] = None,
) -> dict[str, object]:
    with get_session() as session:
        mission = get_owned_mission(
            session,
            vol_id,
            principal,
            requested_owner=owner_subject,
            action="gaussian_viewer",
        )
        descriptor = gaussian_viewer_descriptor(session, mission)
    response.headers["Cache-Control"] = (
        f"private, max-age={VIEWER_DESCRIPTOR_CACHE_SECONDS}"
    )
    response.headers["Vary"] = "Authorization, Cookie"
    return descriptor
