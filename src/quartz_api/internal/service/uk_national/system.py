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
    auth: AuthDependency,
) -> list[Location]:
    """### Get system details for a single GSP or all GSPs.

    Returns an object with system details of a given GSP using the
    _gsp_id_ query parameter, otherwise details for all supply points are provided.

    #### Parameters
    - **gsp_id**: gsp_id of the requested system
    """
    raise NotImplementedError()
