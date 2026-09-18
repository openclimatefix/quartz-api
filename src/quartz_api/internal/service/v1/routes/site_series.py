"""Site time series, single-site and multi-site. Only clearsky for now.

Registered before the site CRUD router: otherwise `/sites/clearsky` would be taken by
`/sites/{site_id}` and fail as an invalid UUID.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..clearsky import clearsky_times, site_clearsky_kw
from ..endpoint_types import (
    CountryParam,
    GenerationValue,
    SiteClearskyMatrix,
    SiteClearskyResponse,
    SitePowerSeries,
    ValidSource,
    ValidWindowEnd,
    ValidWindowStart,
)
from ..helpers import check_country_access, timeseries_window
from ..sites_scoping import get_site, require_org_id

router = APIRouter(tags=["Sites"])


@router.get(
    "/{country}/{source}/sites/clearsky",
    response_model=SiteClearskyMatrix,
)
async def get_sites_clearsky(
    country: CountryParam,
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    site_ids: Annotated[list[UUID] | None, Query(max_length=10)] = None,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteClearskyMatrix:
    """Clear-sky estimates for several sites. Defaults to now (floored to 6h) ± 2 days."""
    check_country_access(auth, country)

    if source != models.EnergyType.SOLAR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Clearsky is only available for solar sites.",
        )

    require_org_id(auth)

    sites = await db.get_locations(
        energy_type=source,
        location_type=models.LocationType.SITE,
        authdata=auth,
    )

    if site_ids is None:
        if len(sites) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"You have {len(sites)} sites; this endpoint returns at most "
                    "10 per request. Pass site_ids to choose which, "
                    "e.g. ?site_ids=<uuid>&site_ids=<uuid>. Site UUIDs come from GET /sites."
                ),
            )
    else:
        sites_by_id = {site.uuid: site for site in sites}
        missing = next((site_id for site_id in site_ids if site_id not in sites_by_id), None)

        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No site found for '{missing}'.",
            )

        sites = [sites_by_id[site_id] for site_id in set(site_ids)]

    sites = sorted(
        sites,
        key=lambda site: (
            str(site.metadata.get("client_site_name", site.name)),
            str(site.uuid),
        ),
    )

    times = clearsky_times(*timeseries_window(start_utc, end_utc))

    return SiteClearskyMatrix(
        times_utc=times,
        sites=[
            SitePowerSeries(
                site_id=site.uuid,
                capacity_kW=site.capacity_kilowatts,
                power_kW=site_clearsky_kw(site, times),
            )
            for site in sites
        ],
    )


@router.get(
    "/{country}/{source}/sites/{site_id}/clearsky",
    response_model=SiteClearskyResponse,
    response_model_exclude_none=True,
)
async def get_site_clearsky(
    country: CountryParam,
    source: ValidSource,
    site_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: ValidWindowEnd = None,
) -> SiteClearskyResponse:
    """Clear-sky generation estimate for one site, every 15 minutes. Defaults to now → +48h."""
    check_country_access(auth, country)

    if source != models.EnergyType.SOLAR:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Clearsky is only available for solar sites.",
        )

    site = await get_site(db, site_id, auth)

    times = clearsky_times(start_utc, end_utc)
    power = site_clearsky_kw(site, times)

    return SiteClearskyResponse(
        site_id=site.uuid,
        capacity_kW=site.capacity_kilowatts,
        values=[
            GenerationValue(time_utc=time, power_kW=power_value)
            for time, power_value in zip(times, power, strict=True)
        ],
    )
