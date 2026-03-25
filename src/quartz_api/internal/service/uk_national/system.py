"""The 'system' FastAPI router object."""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    StorageClientDependency,
)

from .cache import key_builder
from .endpoint_types import Location, gsp_id_map

router = APIRouter(tags=["System"])
log = logging.getLogger(__name__)


@router.get(
    "/gsp/",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder, expire=3600*24)
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
        if 0 not in gsp_id_map:
            raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Location not found",
        )

        nations = await db.get_locations(
            location_uuid=gsp_id_map[0].uuid,
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.NATION,
            authdata={},
        )
        uk_national = nations[0]

        installed_capacity_mw = uk_national.capacity_kilowatts / 1000
        if "capacity_no_degradation_kw" in uk_national.metadata:
            installed_capacity_mw = uk_national.metadata["capacity_no_degradation_kw"] / 1_000

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

    if gsp_id is not None and gsp_id > 0:
        out = [
            Location.from_location(gsp_id_map[gsp_id]),
        ]
        return out

    if gsp_id is None:
        # Get up to date gsp information and update the map
        gsps = await db.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.GSP,
            authdata={},
        )
        for gsp in gsps:
            gsp_id_map[int(gsp.metadata["gsp_id"])] = gsp
            out.append(Location.from_location(gsp))

    out.sort(key=lambda x: x.gsp_id)

    return out
