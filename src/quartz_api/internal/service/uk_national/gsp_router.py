"""The 'gsp' FastAPI router object."""

import asyncio
import datetime as dt
from collections import defaultdict
from typing import TYPE_CHECKING, Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi_cache.decorator import cache
from pydantic import AfterValidator
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .cache import key_builder
from .endpoint_types import (
    ForecastValue,
    GSPYield,
    GSPYieldGroupByDatetime,
    OneDatetimeManyForecastValuesMW,
    convert_list_of_gsp_ids,
)
from .time_utils import (
    limit_end_datetime_by_permissions,
)

if TYPE_CHECKING:
    from uuid import UUID

router = APIRouter(tags=["GSP"])


@router.get(
    "/{gsp_id}/forecast",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_forecasts_for_a_specific_gsp(
    db: models.StorageClientDependency,
    auth: AuthDependency,
    gsp_id: int,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart,
    end_datetime_utc: Annotated[
        dt.datetime,
        Depends(limit_end_datetime_by_permissions),
    ],
    creation_utc_limit: models.UTCDatetime | None = None,
    forecast_horizon_minutes: int | None = None,
) -> list[ForecastValue]:
    """### Get recent forecast values for a specific GSP.

    This route returns the most recent forecast for each _target_time_ for a
    specific GSP.

    The _forecast_horizon_minutes_ parameter allows
    a user to query for a forecast that is made this number, or horizon, of
    minutes before the _target_time_.

    For example, if the target time is 10am today, the forecast made at 2am
    today is the 8-hour forecast for 10am, and the forecast made at 6am for
    10am today is the 4-hour forecast for 10am.

    #### Parameters
    - **gsp_id**: *gsp_id* of the desired forecast
    - **forecast_horizon_minutes**: optional forecast horizon in minutes (ex. 60
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    - **creation_utc_limit**: optional, only return forecasts made before this datetime.
    returns the latest forecast made 60 minutes before the target time)
    """
    # get gsps
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )
    filtered_gsps = [g for g in gsps if int(g.metadata["gsp_id"]) == gsp_id]
    if len(filtered_gsps) != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GSP with gsp_id {gsp_id} not found",
        )
    gsp = filtered_gsps[0]

    pgvs = await db.get_predicted_generation(
        location_uuid=gsp.uuid,
        window_start=start_datetime_utc,
        window_end=end_datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata={},
        created_cutoff=creation_utc_limit,
        forecast_horizon_minutes=forecast_horizon_minutes or 0,
        forecaster_name="blend",
        forecaster_version=None,
    )

    out: list[ForecastValue] = [
        ForecastValue(
            target_time=pp.valid_timestamp,
            expected_power_generation_megawatts=pp.power_kilowatts / 1000,
            expected_power_generation_normalized=pp.power_kilowatts / pp.capacity_kilowatts,
        )
        for pp in pgvs
    ]

    return out


@router.get(
    "/{gsp_id}/pvlive",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_truths_for_a_specific_gsp(
    db: models.StorageClientDependency,
    auth: AuthDependency,
    gsp_id: int,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart,
    end_datetime_utc: models.UTCDatetimeDefaultWindowEnd,
    regime: Annotated[str, AfterValidator(lambda v: v.replace("-", "_"))] = "in-day",
) -> list[GSPYield]:
    """### Get PV_Live values for a specific GSP for yesterday and today.

    The return object is a series of real-time solar energy generation
    from __PV_Live__ for a single GSP.

    Setting the _regime_ parameter to _day-after_ includes
    the previous day's truth values for the GSPs.

    If _regime_ is not specified, the parameter defaults to _in-day_.

    #### Parameters
    - **gsp_id**: _gsp_id_ of the requested forecast
    - **regime**: can choose __in-day__ or __day-after__
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    If not set, defaults to N_HISTORY_DAYS env var, which if not set defaults to yesterday.

    Only 3 days of history is available. If you want to get more PVLive data,
    please use the [PVLive API](https://www.solar.sheffield.ac.uk/api/)
    """
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )
    filtered_gsps = [g for g in gsps if int(g.metadata["gsp_id"]) == gsp_id]
    if len(filtered_gsps) != 1:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"GSP with gsp_id {gsp_id} not found",
        )
    gsp = filtered_gsps[0]

    agvs = await db.get_actual_generation(
        location_uuid=gsp.uuid,
        window_start=start_datetime_utc,
        window_end=end_datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata={},
        observer_name=f"pvlive_{regime}",
    )

    out: list[GSPYield] = [
        GSPYield(
            datetime_utc=v.valid_timestamp,
            solar_generation_kw=v.power_kilowatts,
        )
        for v in agvs
    ]

    return out


@router.get(
    "/forecast/all/",
    response_model=list[OneDatetimeManyForecastValuesMW],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=60 * 30)
async def get_all_available_forecasts(
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_datetime_utc: Annotated[
        models.UTCDatetimeDefaultNowWindowStart,
        AfterValidator(lambda v: pd.Timestamp(v).ceil("30min").to_pydatetime()),
    ],
    gsp_ids: str | None = None,
) -> list[OneDatetimeManyForecastValuesMW]:
    """### Get all forecasts for all GSPs.

    The return object contains a forecast object with system details and
    forecast values for all GSPs.

    This request may take a longer time to load because a lot of data is being
    pulled from the database.

    If _compact_ is set to true, the response will be a list of GSPGenerations objects.
    This return object is significantly smaller, but less readable.

    _gsp_ids_ is a list of integers that correspond to the GSP ids.
    If this is 1,2,3,4 the response will only contain those GSPs.

    #### Parameters
    - **historic**: boolean that defaults to `true`, returning yesterday's and
    today's forecasts for all GSPs
    - **start_datetime_utc**: optional start datetime for the query. e.g '2023-08-12 10:00:00+00:00'
    """
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )
    gsp_ids = convert_list_of_gsp_ids(gsp_ids)
    gsp_uuid_id_map: dict[UUID, int] = {
        gsp.uuid: int(gsp.metadata["gsp_id"]) for gsp in gsps
        if gsp_ids is None or int(gsp.metadata["gsp_id"]) in gsp_ids
    }

    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=gsp_uuid_id_map.keys(),
        snapshot_timestamp_utc=start_datetime_utc,
        energy_type=models.EnergyType.SOLAR,
        forecaster_name="blend",
        authdata={},
    )

    # reorganize results by timestamp
    grouped_data: dict[dt.datetime, dict[int, float]] = defaultdict(dict)
    gsp_ids = list(gsp_uuid_id_map.values())

    # We can zip these because the tasks will return in the same order as they were created
    for gsp_id, predicted_generation_value in zip(gsp_ids, snapshot, strict=True):

        grouped_data[start_datetime_utc][gsp_id] \
            = predicted_generation_value.power_kilowatts / 1000.0

    out: list[OneDatetimeManyForecastValuesMW] = [
        OneDatetimeManyForecastValuesMW(
            datetime_utc=ts,
            forecast_values=dict(sorted(gsp_dict.items())),
        )
        for ts, gsp_dict in grouped_data.items()
    ]

    return out


@router.get(
    "/pvlive/all",
    response_model=list[GSPYieldGroupByDatetime],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=60 * 30)
async def get_truths_for_all_gsps(
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_datetime_utc: models.UTCDatetimeDefaultWindowStart, # TODO update to now
    end_datetime_utc: models.UTCDatetimeDefaultWindowEnd,
    regime: Annotated[str, AfterValidator(lambda v: v.replace("-", "_"))] = "in-day",
    gsp_ids: str | None = None,
) -> list[GSPYieldGroupByDatetime]:
    """### Get PV_Live values for all GSPs for yesterday and today.

    The return object is a series of real-time PV generation estimates or
    truth values from __PV_Live__ for all GSPs.

    Setting the _regime_ parameter to _day-after_ includes
    the previous day's truth values for the GSPs.

    If _regime_ is not specified, the parameter defaults to _in-day_.

    If _compact_ is set to true, the response will be a list of GSPGenerations objects.
    This return object is significantly smaller, but less readable.

    #### Parameters
    - **regime**: can choose __in-day__ or __day-after__
    - **start_datetime_utc**: optional start datetime for the query.
    - **end_datetime_utc**: optional end datetime for the query.
    """
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )

    gsp_ids = convert_list_of_gsp_ids(gsp_ids)
    gsp_uuid_id_map: dict[UUID, int] = {
        gsp.uuid: int(gsp.metadata["gsp_id"]) for gsp in gsps
        if gsp_ids is None or int(gsp.metadata["gsp_id"]) in gsp_ids
    }

    tasks = []
    for gsp_uuid in gsp_uuid_id_map:
        tasks.append(
            asyncio.create_task(
                db.get_actual_generation(
                    location_uuid=str(gsp_uuid),
                    window_start=start_datetime_utc,
                    window_end=end_datetime_utc,
                    energy_type=models.EnergyType.SOLAR,
                    location_type=models.LocationType.GSP,
                    authdata={},
                    observer_name=f"pvlive_{regime}",
                ),
            ),
        )

    results: list[list[models.ActualGenerationValue] | Exception]  = await asyncio.gather(
        *tasks, return_exceptions=True,
    )
    # reorganize results by timestamp
    grouped_data: dict[dt.datetime, dict[int, float]] = defaultdict(dict)
    gsp_ids = list(gsp_uuid_id_map.values())
    # We can zip these because the tasks will return in the same order as they were created
    for gsp_id, gsp_timeseries in zip(gsp_ids, results, strict=True):
        if isinstance(gsp_timeseries, Exception):
            raise gsp_timeseries

        for point in gsp_timeseries:
            grouped_data[point.valid_timestamp][gsp_id] = point.power_kilowatts

    out: list[GSPYieldGroupByDatetime] = [
        GSPYieldGroupByDatetime(
            datetime_utc=ts,
            generation_kw_by_gsp_id=dict(sorted(gsp_dict.items())),
        )
        for ts, gsp_dict in grouped_data.items()
    ]
    return out


