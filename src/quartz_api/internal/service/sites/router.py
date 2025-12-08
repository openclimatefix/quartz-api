"""The 'sites' FastAPI router object and associated routes logic."""

import pathlib
from uuid import UUID

from fastapi import APIRouter
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

@router.get(
    "/sites",
    status_code=status.HTTP_200_OK,
)
async def get_sites(
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> list[models.Site]:
    """Get sites."""
    sites = await db.get_sites(authdata=auth)
    return sites


@router.put(
    "/sites/{site_uuid}",
    response_model=models.SiteProperties,
    status_code=status.HTTP_200_OK)
async def put_site_info(
    site_uuid: UUID,
    site_info: models.SiteProperties,
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> models.SiteProperties:
    """### This route allows a user to update site information for a single site.

    #### Parameters
    - **site_uuid**: The site uuid, for example '8d39a579-8bed-490e-800e-1395a8eb6535'
    - **site_info**: The site informations to update.
        You can update one or more fields at a time. For example :
        {"orientation": 170, "tilt": 35, "capacity_kw": 5}
    """
    site = await db.put_site(site_uuid=site_uuid, site_properties=site_info, authdata=auth)
    return site


@router.get(
    "/sites/{site_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast(
    site_uuid: UUID,
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> list[models.PredictedPower]:
    """Get forecast of a site."""
    forecast = await db.get_site_forecast(site_uuid=site_uuid, authdata=auth)
    return forecast


@router.get(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_generation(
    site_uuid: UUID,
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> list[models.ActualPower]:
    """Get get generation fo a site."""
    generation = await db.get_site_generation(site_uuid=site_uuid, authdata=auth)
    return generation


@router.post(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
async def post_generation(
    site_uuid: UUID,
    generation: list[models.ActualPower],
    db: models.DBClientDependency,
    auth: AuthDependency,
) -> None:
    """Post observed generation data.

    ### This route is used to input actual PV/Wind generation.

    Users will upload actual PV/Wind generation
    readings in kilowatts (kW) at intervals throughout a given day.
    For example: the average power in kW every 5,10,15 or 30 minutes.

    The PowerKW values should be non-negative floating point numbers
    (e.g., 0.0, 1.5, 10.753, etc).

    #### Parameters
    - **site_uuid**: The unique identifier for the site.
    - **generation**: The actual PV generation values for the site.
        You can add one at a time or many. For example:
        {
            "site_uuid": "0cafe3ed-0c5c-4ef0-9a53-e3789e8c8fc9",
            "generation": [
                {
                    "Time": "2024-02-09T17:19:35.986Z",
                    "PowerKW": 1.452
                }
            ]
        }

    All timestamps (Time) are in UTC.

    **Note**: Users should wait up to 1 day(s) to start experiencing the full
    effects from using live PV data.
    """
    await db.post_site_generation(
        site_uuid=site_uuid,
        generation=generation,
        authdata=auth,
    )

