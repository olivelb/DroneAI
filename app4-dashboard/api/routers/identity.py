"""Composition router for the durable identity control plane."""

from fastapi import APIRouter

from .auth import router as session_router
from .identity_credentials import router as credential_router
from .identity_members import router as member_router

router = APIRouter()
router.include_router(session_router)
router.include_router(member_router)
router.include_router(credential_router)
