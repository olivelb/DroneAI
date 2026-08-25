"""Authorized Gaussian viewer discovery routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from shared.database import get_session

from ..gaussian_viewer import gaussian_viewer_descriptor
from ..mission_access import get_owned_mission
from ..security import Principal, require_authenticated

router = APIRouter()


def _accepts_zstd(value: str) -> bool:
    for raw_token in value.split(","):
        encoding, *parameters = raw_token.split(";")
        if encoding.strip().lower() != "zstd":
            continue
        quality = 1.0
        for parameter in parameters:
            name, separator, raw_value = parameter.strip().partition("=")
            if name.lower() == "q" and separator:
                try:
                    quality = float(raw_value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


@router.get("/missions/{vol_id}/gaussians/viewer")
def mission_gaussian_viewer(
    vol_id: str,
    request: Request,
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
        descriptor = gaussian_viewer_descriptor(
            session,
            mission,
            accept_zstd=_accepts_zstd(
                request.headers.get("accept-encoding", "")
            ),
        )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Accept-Encoding"
    return descriptor
