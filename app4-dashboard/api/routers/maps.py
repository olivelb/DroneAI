"""Composition router for the modular geospatial workspace API."""

from fastapi import APIRouter, Depends

from ..security import require_authenticated
from .map_analyses import router as analyses_router
from .map_exports import router as exports_router
from .map_features import router as features_router
from .map_rasters import router as rasters_router

router = APIRouter(
    prefix="/maps",
    tags=["maps"],
    dependencies=[Depends(require_authenticated)],
)
router.include_router(rasters_router)
router.include_router(exports_router)
router.include_router(analyses_router)
router.include_router(features_router)
