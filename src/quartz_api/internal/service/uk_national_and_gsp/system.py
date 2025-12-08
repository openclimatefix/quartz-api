"""The 'uk national and gsp' FastAPI router object and associated routes logic."""


from fastapi import APIRouter
from starlette import status

from quartz_api.internal.models import (
    DBClientDependency,
)
from quartz_api.internal.middleware.auth import AuthDependency

from .pydanitc_models import Location

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
    regions = await db.get_solar_regions()



    locations = []
    for region in regions:

        region_gsp_id = int(region.region_metadata["gsp_id"].number_value)

        if gsp_id is not None and gsp_id != region_gsp_id:
            continue

        if gsp_id == 0:
            gsp_name = "National"
            gsp_group="National"
            region_name = "National"
        else:
            gsp_name = region.region_name
            gsp_group=region.region_name
            # TODO make friendly name, but need to add this to the database
            region_name = region.region_name

        location = Location(label=f"GSP_{gsp_id}",
                            gsp_id=gsp_id,
                            gsp_name=gsp_name,
                            gsp_group=gsp_group,
                            region_name=region_name)

        if "effective_capacity_watts" in region.region_metadata:
            installed_capacity_mw = region.region_metadata["effective_capacity_watts"] / 10**6
            location.installed_capacity_mw = installed_capacity_mw

        # if gsp_id is not None and gsp_id == gsp_id:
        #     return [location]

        locations.append(location)

    # sort by gsp_id
    locations.sort(key=lambda x: x.gsp_id)

    return locations
