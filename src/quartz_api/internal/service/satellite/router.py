"""Historic satellite data router — assembles sub-routers into a single FastAPI router."""

from fastapi import APIRouter

from .routes.ingest import router as ingest_router
from .routes.lookup import router as lookup_router

router = APIRouter(
    prefix="/satellite",
    tags=["Satellite"],
)
router.include_router(lookup_router)
router.include_router(ingest_router)
