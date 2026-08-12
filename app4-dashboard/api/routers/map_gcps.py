"""Composition router for the modular ground-control workspace API."""

from fastapi import APIRouter

from .map_gcp_imports import router as imports_router
from .map_gcp_mutations import router as mutations_router
from .map_gcp_queries import router as queries_router

router = APIRouter()
router.include_router(imports_router)
router.include_router(queries_router)
router.include_router(mutations_router)
