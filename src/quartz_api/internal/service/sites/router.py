"""The 'sites' FastAPI router object and associated routes logic."""

import datetime as dt
import logging
import pathlib
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, HTTPException
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .endpoint_types import Site, SiteActualPower, SitePredictedPower, SiteProperties

log = logging.getLogger(__name__)

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

# Observer name used when reading/writing site-level generation data.
# TODO: should be derived per-site from organisation metadata once DP migration is complete.
SITE_OBSERVER_NAME = "site_api"

# Default forecaster names by energy type, used when a site has no
# explicit forecast_name set in its metadata.
_DEFAULT_FORECASTER: dict[models.EnergyType, str] = {
    models.EnergyType.WIND: "windnet_ad_sites",
    models.EnergyType.SOLAR: "pvnet_extra_ad_sites_ecmwf_satellite_pv_long",
}


def _resolve_forecaster_name(site: models.Location) -> str:
    """Return the forecaster model name to use for a given site.

    Resolution order:
    1. ``site.metadata["forecast_name"]`` — explicit per-site override stored in the DP.
    2. Hardcoded default keyed by ``site.energy_type`` — fallback for any new sites
       that don't yet have forecast_name set in their DP metadata.
    3. Raises ValueError if the energy type is unrecognised and there is no override
       (fail loudly rather than silently serving the wrong model).
    """
    override = site.metadata.get("forecast_name")
    if override:
        log.info("Using forecast_name from metadata for site %s: %s", site.uuid, override)
        return str(override)

    energy_type = site.energy_type
    if energy_type in _DEFAULT_FORECASTER:
        name = _DEFAULT_FORECASTER[energy_type]
        log.info(
            "No forecast_name in metadata for site %s; defaulting to %s",
            site.uuid,
            name,
        )
        return name

    raise ValueError(
        f"Site {site.uuid} has no forecast_name in metadata and an unrecognised "
        f"energy_type '{energy_type}'. Set metadata.forecast_name explicitly."
    )


async def _get_site_with_energy_type(
    db: models.StorageInterface,
    site_uuid: UUID,
    authdata: dict,
) -> models.Location:
    """Fetch a single site and return it with its energy_type populated.

    Calls the DP without an energy_source_filter so both SOLAR and WIND are
    considered in one round-trip. Raises HTTP 404 if not found.
    """
    locs = await db.get_locations(
        energy_type=None,  # no filter → returns SOLAR and WIND
        location_type=models.LocationType.SITE,
        authdata=authdata,
        location_uuid=site_uuid,
    )
    if not locs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No site found for UUID '{site_uuid}'.",
        )
    return locs[0]


@router.get(
    "/sites",
    status_code=status.HTTP_200_OK,
)
async def get_sites(
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> list[Site]:
    """Get all sites (Solar and Wind) visible to the authenticated user."""
    sites = await db.get_locations(
        energy_type=None,  # no filter → returns both SOLAR and WIND
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: list[Site] = [
        Site(
            site_uuid=s.uuid,
            client_site_name=s.metadata.get("client_site_name") or s.name,
            latitude=s.latitude,
            longitude=s.longitude,
            capacity_kw=s.capacity_kilowatts,
            orientation=s.metadata.get("orientation"),
            tilt=s.metadata.get("tilt"),
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
    existing = await _get_site_with_energy_type(db, site_uuid, auth)
    loc: models.Location = models.Location(
        uuid=site_uuid,
        name=site_info.client_site_name,
        capacity_kilowatts=site_info.capacity_kw,
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
        },
    )
    site = await db.put_location(
        location=loc,
        energy_type=existing.energy_type or models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
    out: SiteProperties = SiteProperties(
        latitude=site.latitude,
        longitude=site.longitude,
        capacity_kw=site.capacity_kilowatts,
        orientation=site.metadata.get("orientation"),
        tilt=site.metadata.get("tilt"),
        client_site_name=site.name,
    )
    return out


@router.get(
    "/sites/{site_uuid}/forecast",
    status_code=status.HTTP_200_OK,
    response_model=list[SitePredictedPower],
)
async def get_forecast(
    site_uuid: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
) -> list[SitePredictedPower]:
    """Get forecast of a site (Solar or Wind, auto-detected)."""
    site = await _get_site_with_energy_type(db, site_uuid, auth)

    try:
        forecaster_name = _resolve_forecaster_name(site)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    pgvs = await db.get_predicted_generation(
        location_uuid=site_uuid,
        window_start=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() + dt.timedelta(days=2),
        energy_type=site.energy_type or models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
        forecaster_name=forecaster_name,
    )
    out: list[SitePredictedPower] = [
        SitePredictedPower(
            PowerKW=v.power_kilowatts,
            Time=v.valid_timestamp.astimezone(tz=tz),
            created_time=v.created_timestamp.astimezone(tz=tz) if v.created_timestamp else None,
            forecaster_name=v.forecaster_name,
            forecaster_version=v.forecaster_version,
        )
        for v in pgvs
    ]

    return out


@router.get(
    "/sites/{site_uuid}/generation",
    status_code=status.HTTP_200_OK,
    response_model=list[SiteActualPower],
)
async def get_generation(
    site_uuid: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
) -> list[SiteActualPower]:
    """Get generation of a site (Solar or Wind, auto-detected)."""
    observer_name = SITE_OBSERVER_NAME
    site = await _get_site_with_energy_type(db, site_uuid, auth)
    agvs = await db.get_actual_generation(
        location_uuid=site_uuid,
        window_start=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.now(tz=tz).floor("H").to_pydatetime() + dt.timedelta(days=2),
        energy_type=site.energy_type or models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
        observer_name=observer_name,
    )
    out: list[SiteActualPower] = [
        SiteActualPower(
            PowerKW=v.power_kilowatts,
            Time=v.valid_timestamp.astimezone(tz=tz),
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
    generation: list[SiteActualPower],
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
    # TODO: observer_name should be derived from the site's organization_id metadata
    # in the data platform (via get_locations -> location.metadata["organization_id"]).
    # Hardcoded for now until the DP migration is complete.
    site = await _get_site_with_energy_type(db, site_uuid, auth)
    agvs: list[models.ActualGenerationValue] = [
        models.ActualGenerationValue(
            power_kilowatts=g.PowerKW,
            valid_timestamp=g.Time,
            location_uuid=site_uuid,
            capacity_kilowatts=0,  # NOTE: This is ignored when writing
            observer_name=SITE_OBSERVER_NAME,
        )
        for g in generation
    ]

    await db.put_actual_generation(
        location_uuid=site_uuid,
        generation_values=agvs,
        energy_type=site.energy_type or models.EnergyType.SOLAR,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )
