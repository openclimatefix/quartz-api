"""The sites API router — assembles sub-routers into a single FastAPI router."""

from fastapi import APIRouter

from .routes.sites import router as sites_router

router = APIRouter()
router.include_router(sites_router)
