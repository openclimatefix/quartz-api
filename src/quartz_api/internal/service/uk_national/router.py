"""The 'uk national and gsp' FastAPI router object and associated routes logic."""


from importlib.metadata import version

from fastapi import APIRouter

from .description import description
from .gsp import router as gsp_router
from .national import router as national_router
from .status import router as status_router
from .system import router as system_router

router = APIRouter()
version = version("quartz-api")

general_routes_prefix = "/v0/solar/GB"

router.include_router(
    national_router, prefix=f"{general_routes_prefix}/national", tags=["National"],
)
router.include_router(gsp_router, prefix=f"{general_routes_prefix}/gsp", tags=["GSP"])
router.include_router(status_router, prefix=f"{general_routes_prefix}/status")

router.include_router(system_router, prefix="/v0/system/GB", tags=["System"])


@router.get("/")
def get_api_information() -> dict:
    """### Get basic Quartz Solar API information.

    Returns a json object with basic information about the Quartz Solar API.
    """
    return {
        "title": "Quartz Solar API",
        "version": version,
        "description": description,
        "documentation": "https://api.quartz.solar/docs",
        "swagger ui": "https://api.quartz.solar/swagger",
    }
