"""The 'substations' FastAPI router object and associated routes logic."""

import pathlib
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .time_utils import floor_30_minutes_dt

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

@router.get(
    "/substations",
    status_code=status.HTTP_200_OK,
)
async def get_substations(
    request: Request,
    db: models.DBClientDependency,
    _: AuthDependency,
    substation_type: Literal["primary"] = "primary", # noqa: ARG001
) -> list[models.Substation]:
    """Get all substations.

    Note that currently only 'primary' substations are supported.
    """
    substations = await db.get_substations(authdata={}, traceid=request.state.trace_id)
    return substations


@router.get(
    "/substations/{substation_uuid:uuid}",
    status_code=status.HTTP_200_OK,
)
async def get_substation(
    request: Request,
    substation_uuid: UUID,
    db: models.DBClientDependency,
    _: AuthDependency,
) -> models.SubstationProperties:
    """Get a substation by UUID."""
    substation = await db.get_substation(
        location_uuid=substation_uuid,
        authdata={},
        traceid=request.state.trace_id,
    )
    return substation

@router.get(
    "/substations/{substation_uuid:uuid}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_substation_forecast(
    request: Request,
    substation_uuid: UUID,
    db: models.DBClientDependency,
    _: AuthDependency,
    tz: models.TZDependency,
) -> list[models.PredictedPower]:
    """Get forecasted generation values of a substation."""
    forecast = await db.get_substation_forecast(
        location_uuid=substation_uuid,
        authdata={},
        traceid=request.state.trace_id,
    )
    forecast = [
        value.to_timezone(tz=tz)
        for value in forecast
    ]
    return forecast


@router.get(
    "/substations/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_all_substation_forecast_at_one_timestamp(
    request: Request,
    db: models.DBClientDependency,
    _: AuthDependency,
    datetime_utc: datetime | None = None) -> models.OneDatetimeManyForecastValues:
    """Get forecasted generation values of all substations at a specific timestamp."""
    if datetime_utc is None:
        datetime_utc = floor_30_minutes_dt(datetime.now(UTC))

    # 1. Get all substation locations
    substations = await db.get_substations(authdata={}, traceid=request.state.trace_id)
    gsp_ids = [substation.metadata["gsp_id"] for substation in substations]
    gsp_ids = sorted(set(gsp_ids))

    # 2. get the relevant gsps locations
    all_gsp_regions = await db.get_solar_regions(type="gsp")
    gsp_regions = [region for region in all_gsp_regions
                   if region.region_metadata["gsp_id"] in gsp_ids]

    # 3. get gsp forecasts
    forecasts = await db.get_forecast_for_multiple_locations_one_timestamp(
        location_uuids=[gsp.region_metadata["location_uuid"] for gsp in gsp_regions],
        authdata={},
        datetime_utc=datetime_utc,
    )

    # 4. Add substation forecasts
    for substation in substations:
        # find gsp region
        gsp_id = substation.metadata.get("gsp_id")
        gsp_region = next((gsp for gsp in gsp_regions
                        if gsp.region_metadata["gsp_id"] == gsp_id), None)
        if gsp_region is None:
            continue

        # get forecast value
        gsp_location_uuid = gsp_region.region_metadata["location_uuid"]
        gsp_forecast_value = forecasts.forecast_values_kw.get(gsp_location_uuid, 0.0)
        if substation.capacity_kw == 0:
            continue
        scale_factor = substation.capacity_kw / \
            (gsp_region.region_metadata["effective_capacity_watts"] / 1000)
        substation_forecast_value = round(gsp_forecast_value * scale_factor,3)

        # assign to substation
        forecasts.forecast_values_kw[str(substation.substation_uuid)] = substation_forecast_value

    # 5. remove GSP forecasts
    for gsp in gsp_regions:
        gsp_location_uuid = gsp.region_metadata["location_uuid"]
        forecasts.forecast_values_kw.pop(gsp_location_uuid, None)

    return forecasts
