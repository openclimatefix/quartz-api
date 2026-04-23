"""The v1 API router — assembles sub-routers into a single FastAPI router."""

from fastapi import APIRouter

from .routes.capability import router as capability_router
from .routes.forecasts import router as forecasts_router
from .routes.generation import router as generation_router
from .routes.regions import router as regions_router

router = APIRouter()
router.include_router(capability_router)
router.include_router(regions_router)
router.include_router(forecasts_router)
router.include_router(generation_router)
