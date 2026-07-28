"""Browser-session endpoints for the dashboard API."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .. import security


router = APIRouter(prefix="/auth", tags=["authentication"])


class SessionRequest(BaseModel):
    api_key: str = Field(min_length=32, max_length=4096)


@router.post("/session")
def create_session(payload: SessionRequest, response: Response):
    principal = security.authenticate_api_key(payload.api_key)
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid API credential",
            headers={"WWW-Authenticate": "Bearer"},
        )
    max_age = int(os.getenv("DRONEAI_SESSION_MAX_AGE_SECONDS", "28800"))
    if max_age <= 0:
        raise RuntimeError("DRONEAI_SESSION_MAX_AGE_SECONDS must be positive")
    response.set_cookie(
        key=security.SESSION_COOKIE_NAME,
        value=security.issue_session_token(principal, max_age),
        max_age=max_age,
        httponly=True,
        secure=security.is_production(),
        samesite="lax",
        path="/",
    )
    return {
        "subject": principal.subject,
        "role": principal.role,
        "expires_in_seconds": max_age,
    }


@router.get("/session")
def read_session(
    principal: security.Principal = Depends(security.require_authenticated),
):
    return {
        "subject": principal.subject,
        "role": principal.role,
    }


@router.delete("/session")
def delete_session(response: Response):
    response.delete_cookie(
        key=security.SESSION_COOKIE_NAME,
        path="/",
        secure=security.is_production(),
        httponly=True,
        samesite="lax",
    )
    return {"status": "ok"}
