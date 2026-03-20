"""The 'national' FastAPI router object."""

import datetime as dt
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Request
from fastapi_cache.decorator import cache
from pydantic import AfterValidator
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter

from .cache import key_builder
from .endpoint_types import (
    InputDataLastUpdated,
    Location,
    MLModel,
    ModelName,
    NationalForecast,
    NationalForecastValue,
    NationalYield,
)
from .time_utils import limit_end_datetime_by_permissions

router = APIRouter(tags=["National"])

model_names_external_to_internal = {
    "blend": "blend",
    "pvnet_intraday": "pvnet_v2",
    "pvnet_day_ahead": "pvnet_day_ahead",
    "pvnet_intraday_ecmwf_only": "pvnet_ecmwf",
    "pvnet_intraday_met_office_only": "pvnet_ukv_only",
    "pvnet_intraday_sat_only": "pvnet_sat_only",
}

# NOTE: We don't have to explicitly ask for UTC in the time parameters here,
# we can simply enforce timezones and coerce from there (which is what we're doing).
@router.get(
    "/forecast",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3600/hour")
@cache(key_builder=key_builder)
async def get_national_forecast(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart,
    end_datetime_utc: Annotated[
        models.UTCDatetimeDefaultWindowEnd,
        Depends(limit_end_datetime_by_permissions),
    ],
    creation_limit_utc: models.UTCDatetime | None = None,
    forecast_horizon_minutes: int | None = None,
    include_metadata: bool = False,
    model_name: ModelName = ModelName.blend,
    trend_adjuster_on: bool | None = True,
) -> NationalForecast | list[NationalForecastValue]:
    """Fetch national forecasts.

    This route returns the most recent forecast for each _target_time_.

    The _forecast_horizon_minutes_ parameter allows
    a user to query for a forecast that is made this number, or horizon, of
    minutes before the _target_time_.

    For example, if the target time is 10am today, the forecast made at 2am
    today is the 8-hour forecast for 10am, and the forecast made at 6am for
    10am today is the 4-hour forecast for 10am.

    #### Parameters
    - **forecast_horizon_minutes**: optional forecast horizon in minutes (ex.
    60 returns the forecast made an hour before the target time)
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    - **creation_limit_utc**: optional, only return forecasts made before this datetime.
    Note you can only go 7 days back at the moment
    - **model_name**: optional, specify which model to use for the forecast.
    Options: blend (default), pvnet_intraday, pvnet_day_ahead, pvnet_intraday_ecmwf_only
    - **trend_adjuster_on**: optional, default is True.
    The forecast is adjusted depending on trends in the last week.
    This should remove systematic errors.
    Warning if set to False, the forecast accuracy will likely decrease.

    Returns: The national forecast data.

    """
    # get model name
    model_name = model_names_external_to_internal[model_name]
    if trend_adjuster_on:
        model_name = model_name + "_adjust"

    # get national location UUID and and set forecast horizon
    nations = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata=auth,
    )
    filtered_nations = [n for n in nations if n.name == "uk"]
    if len(filtered_nations) != 1:
        raise ValueError("No nation with name 'uk' found in database.")
    nation = filtered_nations[0]

    pgvs = await db.get_predicted_generation(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        window_start=start_datetime_utc,
        window_end=end_datetime_utc,
        created_cutoff=creation_limit_utc,
        forecast_horizon_minutes=forecast_horizon_minutes or 0,
        forecaster_name=model_name,
        authdata={},
        location_uuid=nation.uuid,
    )

    out: list[NationalForecastValue] = [
        NationalForecastValue(
            target_time=v.valid_timestamp,
            expected_power_generation_megawatts=v.power_kilowatts / 1000,
            plevels={
                "plevel_10": v.plevels_kilowatts.get("p10") / 1000
                    if v.plevels_kilowatts.get("p10") is not None else None,
                "plevel_90": v.plevels_kilowatts.get("p90") / 1000
                    if v.plevels_kilowatts.get("p90") is not None else None,
            },
        )
        for v in pgvs
    ]

    if not include_metadata:
        return out

    else:
        # Legacy inputdata,
        # In nowcasting_datamodel, we get this from the database
        input_data = format_metadata(pgvs[-1].metadata)

        # get version
        version = pgvs[-1].metadata.get("app_version", pgvs[-1].forecaster_version)

        national_forecast = NationalForecast(
            location=Location.from_location(nation),
            model=MLModel(
                name=pgvs[0].forecaster_name,
                version=version,
            ),
            forecast_creation_time=pgvs[0].created_timestamp,
            initialization_datetime_utc=pgvs[0].init_timestamp,
            forecast_values=out,
            input_data_last_updated=input_data,
        )

        return national_forecast


@router.get(
    "/pvlive",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3600/hour")
@cache(key_builder=key_builder)
async def get_national_pvlive(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency,
    regime: Annotated[str, AfterValidator(lambda v: v.replace("-", "_"))] = "in-day",
) -> list[NationalYield]:
    """### Get national PV_Live values for yesterday and/or today.

    Returns a series of real-time solar energy generation readings from
    PV_Live for all of Great Britain.

    _In-day_ values are PV generation estimates for the current day,
    while _day-after_ values are
    updated PV generation truths for the previous day along with
    _in-day_ estimates for the current day.

    If nothing is set for the _regime_ parameter, the route will return
    _in-day_ values for the current day.

    #### Parameters
    - **regime**: can choose __in-day__ or __day-after__

    """
    # get national location UUID and and set forecast horizon
    nations = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata=auth,
    )
    filtered_nations = [n for n in nations if n.name == "uk"]
    if len(filtered_nations) != 1:
        raise ValueError("No nation with name 'uk' found in database.")
    nation = filtered_nations[0]

    agvs = await db.get_actual_generation(
        location_uuid=nation.uuid,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        window_start=pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2),
        observer_name=f"pvlive_{regime}",
        authdata={},
    )

    out: list[NationalYield] = [
        NationalYield(
            datetime_utc=v.valid_timestamp,
            solar_generation_kw=v.power_kilowatts,
        )
        for v in agvs
    ]

    return out


def format_metadata(metadata: dict) -> InputDataLastUpdated:
    """Format metadata dictionary into InputDataLastUpdated object."""
    old = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)
    gsp = metadata.get("gsp_last_updated", old)
    satellite = metadata.get("satellite_last_updated", old)

    # the nwp keys could be nwp_ukv_last_updated, nwp_ecwmwf_last_updated, or nwp_last_updated
    nwp = old
    for nwp_key in [k for k in metadata if "nwp" in k]:
        nwp = max([metadata.get(nwp_key, old)])
    return InputDataLastUpdated(gsp=gsp, nwp=nwp, pv=old, satellite=satellite)
