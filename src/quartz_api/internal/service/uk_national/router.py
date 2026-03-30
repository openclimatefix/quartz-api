"""The 'uk national and gsp' FastAPI router object and associated routes logic."""

from importlib.metadata import version

from fastapi import APIRouter

from .gsp_router import router as gsp_router
from .national_router import router as national_router
from .status import router as status_router, router_check_last_forecast_run
from .system import router as system_router

router = APIRouter()
version = version("quartz-api")

general_routes_prefix = "/v0/solar/GB"

router.include_router(
    national_router,
    prefix=f"{general_routes_prefix}/national",
    tags=["National"],
)
router.include_router(gsp_router, prefix=f"{general_routes_prefix}/gsp", tags=["GSP"])
router.include_router(status_router, prefix=f"{general_routes_prefix}/status")
router.include_router(router_check_last_forecast_run, prefix=f"{general_routes_prefix}")

router.include_router(system_router, prefix="/v0/system/GB", tags=["System"])
