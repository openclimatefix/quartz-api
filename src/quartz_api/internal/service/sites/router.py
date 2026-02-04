"""The 'sites' FastAPI router object and associated routes logic."""

import datetime as dt
import pathlib
from uuid import UUID

import pandas as pd
from fastapi import APIRouter
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .endpoint_types import ActualPower, PredictedPower, Site, SiteProperties

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

# TODO: I'm not sure if the inputs here are strictly solar only. If not, I need to find a way to
# work out which type is desired.


@router.get(
    "/sites",
    status_code=status.HTTP_200_OK,
)
async def get_sites(
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> list[Site]:
    """Get sites."""
    sites = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: list[Site] = [
        Site(
            site_uuid=s.uuid,
            client_site_name=s.name,
            latitude=s.latitude,
            longitude=s.longitude,
            capacity_kW=s.capacity_kilowatts,
            orientation=s.metadata.get("orientation"),
            tilt=s.metadata.get("tilt"),
            metadata=s.metadata,
        )
        for s in sites
    ]
    return out


@router.put("/sites/{site_uuid}", response_model=SiteProperties, status_code=status.HTTP_200_OK)
async def put_site_info(
    site_uuid: UUID,
    site_info: SiteProperties,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> SiteProperties:
    """### This route allows a user to update site information for a single site.

    #### Parameters
    - **site_uuid**: The site uuid, for example '8d39a579-8bed-490e-800e-1395a8eb6535'
    - **site_info**: The site informations to update.
        You can update one or more fields at a time. For example :
        {"orientation": 170, "tilt": 35, "capacity_kw": 5}
    """
    loc: models.Location = models.Location(
        uuid=site_uuid,
        name=site_info.client_site_name,
        capacity_kilowatts=site_info.capacity_kW,
        latitude=site_info.latitude,
        longitude=site_info.longitude,
        metadata={
            k: v
            for k, v in {
                "orientation": site_info.orientation,
                "tilt": site_info.tilt,
                "client_site_name": site_info.client_site_name,
            }.items()
            if v is not None
        }
        | site_info.metadata,
    )
    site = await db.put_location(
        location=loc,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: SiteProperties = SiteProperties(
        latitude=site.latitude,
        longitude=site.longitude,
        capacity_kW=site.capacity_kilowatts,
        orientation=site.metadata.get("orientation"),
        tilt=site.metadata.get("tilt"),
        client_site_name=site.name,
        metadata=site.metadata,
    )
    return out


@router.get(
    "/sites/{site_uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast(
    site_uuid: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
) -> list[PredictedPower]:
    """Get forecast of a site."""
    pgvs = await db.get_predicted_generation(
        location_uuid=site_uuid,
        window_start=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() + dt.timedelta(days=2),
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: list[PredictedPower] = [
        PredictedPower(
            power_kW=v.power_kilowatts,
            time=v.valid_timestamp.astimezone(tz=tz),
            created_time=v.created_timestamp.astimezone(tz=tz),
            initialization_timestamp_utc=v.init_timestamp.astimezone(tz=tz),
            forecaster_name=v.forecaster_name,
            forecaster_version=v.forecaster_version,
            plevel_kW=v.plevels_kilowatts,
        )
        for v in pgvs
    ]

    return out


@router.get(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_generation(
    site_uuid: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
) -> list[ActualPower]:
    """Get get generation fo a site."""
    agvs = await db.get_actual_generation(
        location_uuid=site_uuid,
        window_start=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() + dt.timedelta(days=2),
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: list[ActualPower] = [
        ActualPower(
            PowerKW=v.power_kilowatts,
            Time=v.valid_timestamp.astimezone(tz=tz),
            location_uuid=str(v.location_uuid),
        )
        for v in agvs
    ]
    return out


@router.post(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
)
async def post_generation(
    site_uuid: UUID,
    generation: list[ActualPower],
    db: models.StorageClientDependency,
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
    agvs: list[models.ActualGenerationValue] = [
        models.ActualGenerationValue(
            power_kilowatts=g.PowerKW,
            valid_timestamp=g.Time,
            location_uuid=site_uuid,
            capacity_kilowatts=0,  # NOTE: This is ignored when writing
            observer_name="ruvnl",  # TODO: presumably this could change based on user input
        )
        for g in generation
    ]

    await db.put_actual_generation(
        location_uuid=site_uuid,
        generation_values=agvs,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
