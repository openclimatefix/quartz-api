"""The 'system' FastAPI router object."""

import logging

from fastapi import APIRouter, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.models import (
    StorageClientDependency,
)

from .cache import get_gsps, key_builder
from .endpoint_types import Location

router = APIRouter(tags=["System"])
log = logging.getLogger(__name__)


@router.get(
    "/gsp/",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3600/hour")
@limiter.limit("10/second")
@cache(key_builder=key_builder)
async def get_system_details(
    request: Request,  # noqa: ARG001
    db: StorageClientDependency,
    auth: AuthDependency,  # noqa
    gsp_id: int | None = None,
) -> list[Location]:
    """### Get system details for a single GSP or all GSPs.

    Returns an object with system details of a given GSP using the
    _gsp_id_ query parameter, otherwise details for all supply points are provided.

    #### Parameters
    - **gsp_id**: gsp_id of the requested system
    """
    out: list[Location] = []

    if gsp_id is None or gsp_id == 0:
        nations = await db.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.NATION,
            authdata={},
        )

        uk_national = [n for n in nations if n.name == "uk"]
        national = uk_national[0]
        installed_capacity_mw = national.capacity_kilowatts / 1000
        if "capacity_no_degradation_kw" in national.metadata:
            installed_capacity_mw = national.metadata["capacity_no_degradation_kw"] / 1_000

        # Why not use from_location here?
        location = Location(
            label="National-GB",
            gsp_id=0,
            gsp_name="National",
            gsp_group="National",
            region_name="National",
            installed_capacity_mw=installed_capacity_mw,
        )
        out.append(location)
        log.info("Fetched national system details")

    if gsp_id is not None and gsp_id == 0:
        return out

    gsps = await get_gsps(db=db, auth={})
    log.info(f"Fetched {len(gsps)} GSPs")

    if gsp_id is not None and gsp_id > 0:
        out = [
            Location.from_location(gsp)
            for gsp in gsps
            if "gsp_id" in gsp.metadata
            and int(gsp.metadata["gsp_id"]) == gsp_id
        ]

    if gsp_id is None:
        out.extend([Location.from_location(gsp) for gsp in gsps])

    out.sort(key=lambda x: x.gsp_id)

    return out
