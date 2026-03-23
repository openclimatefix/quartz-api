"""The 'system' FastAPI router object."""

from fastapi import APIRouter, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.models import (
    StorageClientDependency,
)

from .cache import key_builder
from .endpoint_types import Location

router = APIRouter(tags=["System"])


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
    # National
    regions = await db.get_locations(energy_type=models.EnergyType.SOLAR,
                                     location_type=models.LocationType.NATION,
                                     authdata={})

    uk_national = [r for r in regions if r.name == "uk"]
    national = uk_national[0]
    installed_capacity_mw = national.capacity_kilowatts / 1000
    if "capacity_no_degradation_kw" in national.metadata:
        installed_capacity_mw = national.metadata["capacity_no_degradation_kw"] / 1_000

    location = Location(
        label="National-GB",
        gsp_id=0,
        gsp_name="National",
        gsp_group="National",
        region_name="National",
        installed_capacity_mw=installed_capacity_mw,
    )

    if gsp_id == 0:
        return [location]

    # GSP
    regions = await db.get_locations(energy_type=models.EnergyType.SOLAR,
                                     location_type=models.LocationType.GSP,
                                     authdata={})

    locations = [location]
    for region in regions:
        location = Location.from_location(region)

        if gsp_id is not None and gsp_id != location.gsp_id:
            continue

        if gsp_id is not None and gsp_id == location.gsp_id:
            return [location]

        if "capacity_no_degradation_kw" in region.metadata:
            location.installed_capacity_mw \
                = region.metadata["capacity_no_degradation_kw"] / 1_000

        locations.append(location)

    locations.sort(key=lambda x: x.gsp_id)

    return locations
