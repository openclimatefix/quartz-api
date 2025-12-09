"""The 'system' FastAPI router object."""


from fastapi import APIRouter
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    DBClientDependency,
)

from .pydantic_models import Location

router = APIRouter(tags=["System"])


@router.get(
    "/gsp",
    status_code=status.HTTP_200_OK,
)
async def get_system_details(
    db: DBClientDependency,
    auth: AuthDependency,  # noqa TODO use auth
    gsp_id: int | None = None,
) -> list[Location]:
    """### Get system details for a single GSP or all GSPs.

    Returns an object with system details of a given GSP using the
    _gsp_id_ query parameter, otherwise details for all supply points are provided.

    #### Parameters
    - **gsp_id**: gsp_id of the requested system
    """
    # National
    regions = await db.get_solar_regions(type="nation")

    national = regions[0]
    installed_capacity_mw = national.region_metadata["effective_capacity_watts"] / 10**6

    location = Location(label="National-GB",
                        gsp_id=0,
                        gsp_name="National",
                        gsp_group="National",
                        region_name="National",
                        installed_capacity_mw=installed_capacity_mw)


    if gsp_id == 0:
        return [location]

    # GSP
    regions = await db.get_solar_regions(type="gsp")

    locations = [location]
    for region in regions:

        region_gsp_id = int(region.region_metadata["gsp_id"].number_value)
        installed_capacity_mw = region.region_metadata["effective_capacity_watts"] / 10**6
        if "full_name" in region.region_metadata:
            full_name = region.region_metadata["full_name"].string_value
        else:
            full_name = region.region_name

        if gsp_id is not None and gsp_id != region_gsp_id:
            continue

        gsp_name = region.region_name
        gsp_group=region.region_name
        region_name = full_name

        location = Location(label=f"GSP_{region_gsp_id}",
                            gsp_id=region_gsp_id,
                            gsp_name=gsp_name,
                            gsp_group=gsp_group,
                            region_name=region_name,
                            installed_capacity_mw=installed_capacity_mw)

        if gsp_id is not None and gsp_id == region_gsp_id:
            return [location]

        locations.append(location)

    # sort by gsp_id
    locations.sort(key=lambda x: x.gsp_id)

    return locations

