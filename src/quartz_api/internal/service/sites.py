from starlette import status

from fastapi import APIRouter, Depends

from quartz_api.internal import ActualPower, PredictedPower, Site, SiteProperties
from quartz_api.internal.service.database_client import DBClientDependency
from quartz_api.internal.service.auth import auth

router = APIRouter(
    tags=["Sites"],
)


@router.get(
    "/sites",
    status_code=status.HTTP_200_OK,
)
def get_sites(db: DBClientDependency, auth: dict = Depends(auth)) -> list[Site]:
    """Get sites"""

    # get email from auth
    email = auth["https://openclimatefix.org/email"]

    sites = db.get_sites(email=email)

    return sites


@router.put("/sites/{site_uuid}", response_model=SiteProperties, status_code=status.HTTP_200_OK)
def put_site_info(
    site_uuid: str,
    site_info: SiteProperties,
    db: DBClientDependency,
    auth: dict = Depends(auth),
) -> SiteProperties:
    """
    ### This route allows a user to update site information for a single site.

    #### Parameters
    - **site_uuid**: The site uuid, for example '8d39a579-8bed-490e-800e-1395a8eb6535'
    - **site_info**: The site informations to update.
        You can update one or more fields at a time. For example :
        {"orientation": 170, "tilt": 35, "capacity_kw": 5}
    """

    # get email from auth
    email = auth["https://openclimatefix.org/email"]

    site = db.put_site(site_uuid=site_uuid, site_properties=site_info, email=email)

    return site

@router.get(
    "/sites/{site_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
def get_forecast(
    site_uuid: str, db: DBClientDependency, auth: dict = Depends(auth)
) -> list[PredictedPower]:
    """Get forecast of a site"""

    # get email from auth
    email = auth["https://openclimatefix.org/email"]

    forecast = db.get_site_forecast(site_uuid=site_uuid, email=email)

    return forecast


@router.get(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
def get_generation(
    site_uuid: str, db: DBClientDependency, auth: dict = Depends(auth)
) -> list[ActualPower]:
    """Get get generation fo a site"""

    # get email from auth
    email = auth["https://openclimatefix.org/email"]

    generation = db.get_site_generation(site_uuid=site_uuid, email=email)

    return generation


@router.post(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
def post_generation(
    site_uuid: str,
    generation: list[ActualPower],
    db: DBClientDependency,
    auth: dict = Depends(auth),
):
    """Get sites
    
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

    # get email from auth
    email = auth["https://openclimatefix.org/email"]

    db.post_site_generation(site_uuid=site_uuid, generation=generation, email=email)
