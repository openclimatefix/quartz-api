"""The 'national' FastAPI router object."""

import datetime as dt
import logging
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, Request
from fastapi_cache.decorator import cache
from pydantic import AfterValidator
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.service.uk_national.metadata import format_metadata

from .cache import key_builder
from .endpoint_types import (
    Location,
    MLModel,
    ModelName,
    NationalForecast,
    NationalForecastValue,
    NationalYield,
    gsp_id_map,
    model_names_external_to_internal,
)
from .time_utils import limit_end_datetime_by_permissions

log = logging.getLogger(__name__)

router = APIRouter()

FORECASTER_VERSION_BLEND = "1.3.0"
FORECASTER_VERSION_PVNET = "2.8.0"


# NOTE: We don't have to explicitly ask for UTC in the time parameters here,
# we can simply enforce timezones and coerce from there (which is what we're doing).
@router.get(
    "/forecast",
    response_model=NationalForecast | list[NationalForecastValue],
    status_code=status.HTTP_200_OK,
    openapi_extra={
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "examples": {
                            "Default (include_metadata=false)": {
                                "summary": "List of forecast values (default)",
                                "value": [
                                    {
                                        "targetTime": "2026-03-26T12:00:00+00:00",
                                        "expectedPowerGenerationMegawatts": 4.2,
                                        "plevels": {"plevel_10": 3.8, "plevel_90": 4.6},
                                    },
                                ],
                            },
                            "With metadata (include_metadata=true)": {
                                "summary": "Full NationalForecast object with location and model info", # noqa: E501
                                "value": {
                                    "location": {"label": "national", "gspId": 0},
                                    "model": {"name": "blend_adjust", "version": "1.3.0"},
                                    "forecastCreationTime": "2026-03-26T06:00:00+00:00",
                                    "forecastValues": [
                                        {
                                            "targetTime": "2026-03-26T12:00:00+00:00",
                                            "expectedPowerGenerationMegawatts": 4.2,
                                            "plevels": {"plevel_10": 3.8, "plevel_90": 4.6},
                                        },
                                    ],
                                    "inputDataLastUpdated": {
                                        "gsp": "2026-03-26T05:30:00+00:00",
                                        "nwp": "2026-03-26T04:00:00+00:00",
                                        "pv": "1970-01-01T00:00:00+00:00",
                                        "satellite": "2026-03-26T05:45:00+00:00",
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
)
@cache(key_builder=key_builder)
async def get_national_forecast(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency, # noqa: ARG001
    end_datetime_utc: Annotated[
        models.UTCDatetimeDefaultWindowEndNonAware,
        Depends(limit_end_datetime_by_permissions),
    ],
    start_datetime_utc: models.UTCDatetimeNonAware | None = None,
    creation_limit_utc: models.UTCDatetimeNonAware | None = None,
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
    # In the legacy database, when metadata=true,
    # we get from from now - rounded up to nearest 30 mins, less 3 days.
    if start_datetime_utc is None:
        start_datetime_utc \
            = pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2)
        if include_metadata:
            start_datetime_utc \
                = pd.Timestamp.utcnow().ceil("30min").to_pydatetime() - dt.timedelta(days=3)

    windows: list[tuple[dt.datetime, dt.datetime]] = [(start_datetime_utc, end_datetime_utc)]
    if end_datetime_utc - start_datetime_utc > dt.timedelta(days=7):
        windows = [
            (
                start_datetime_utc + dt.timedelta(days=i),
                min(start_datetime_utc + dt.timedelta(days=i+7, seconds=-1), end_datetime_utc),
            )
            for i in range(0, (end_datetime_utc - start_datetime_utc).days, 7)
        ]

    # get model name
    model_name_str = model_names_external_to_internal[model_name]
    if trend_adjuster_on:
        model_name_str += "_adjust"

    # get national location
    locations = await db.get_locations(
        location_uuid=gsp_id_map[0].uuid,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata={},
    )
    uk_loc = locations[0]


    all_pgvs: list[models.PredictedGenerationValue] = []
    for window in windows:
        pgvs = await db.get_predicted_generation(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.NATION,
            window_start=window[0],
            window_end=window[1],
            created_cutoff=creation_limit_utc,
            forecast_horizon_minutes=forecast_horizon_minutes or 0,
            forecaster_name=model_name_str,
            forecaster_version=FORECASTER_VERSION_BLEND \
                if model_name == ModelName.blend else FORECASTER_VERSION_PVNET,
            authdata={},
            location_uuid=uk_loc.uuid,
        )
        all_pgvs.extend(pgvs)
        log.info(f"Fetched {len(pgvs)} predicted generation values")


    all_pgvs = sorted(all_pgvs, key=lambda x: x.valid_timestamp, reverse=False)
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
        for v in all_pgvs
    ]

    # legacy bug in Old API
    if forecast_horizon_minutes is not None:
        out = [v for v in out if v.target_time < end_datetime_utc]

    if not include_metadata:
        return out

    else:

        # Legacy inputdata,
        # In nowcasting_datamodel, we get this from the database
        input_data = format_metadata(pgvs[-1].metadata)

        # get version
        version = pgvs[-1].metadata.get("app_version", pgvs[-1].forecaster_version)

        national_forecast = NationalForecast(
            location=Location.from_location(uk_loc),
            model=MLModel(
                name=pgvs[-1].forecaster_name,
                version=version,
            ),
            forecast_creation_time=pgvs[-1].created_timestamp,
            initialization_datetime_utc=pgvs[-1].init_timestamp,
            forecast_values=out,
            input_data_last_updated=input_data,
        )

        return national_forecast


@router.get(
    "/pvlive",
    response_model=list[NationalYield],
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_national_pvlive(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency, # noqa: ARG001
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
    locations = await db.get_locations(
        location_uuid=gsp_id_map[0].uuid,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata={},
    )
    uk_loc = locations[0]

    agvs = await db.get_actual_generation(
        location_uuid=uk_loc.uuid,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        window_start=pd.Timestamp.utcnow().floor("6h").to_pydatetime() - dt.timedelta(days=2),
        window_end=pd.Timestamp.utcnow().floor("6h").to_pydatetime() + dt.timedelta(days=2),
        observer_name=f"pvlive_{regime}",
        authdata={},
    )
    log.info(f"Fetched {len(agvs)} generation values")

    out: list[NationalYield] = [
        NationalYield(
            datetime_utc=v.valid_timestamp,
            solar_generation_kw=v.power_kilowatts,
        )
        for v in agvs
    ]

    # lets make sure the latest timestamps are first
    out.sort(key=lambda x: x.datetime_utc, reverse=True)

    return out


