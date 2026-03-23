"""The 'gsp' FastAPI router object."""

import asyncio
import datetime as dt
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_cache.decorator import cache
from pydantic import AfterValidator
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.middleware.ratelimit import limiter
from quartz_api.internal.service.uk_national.metadata import format_metadata

from .cache import key_builder
from .endpoint_types import (
    Forecast,
    ForecastValue,
    GSPYield,
    GSPYieldGroupByDatetime,
    Location,
    MLModel,
    OneDatetimeManyForecastValuesMW,
    convert_list_of_gsp_ids,
)
from .time_utils import (
    limit_end_datetime_by_permissions,
)

if TYPE_CHECKING:
    from uuid import UUID

log = logging.getLogger(__name__)

GSP_FORECASTER_NAME = "blend"
GSP_FORECASTER_VERSION = "1.3.0"

router = APIRouter(tags=["GSP"])

async def get_gsps(
        db: models.StorageClientDependency, auth: AuthDependency) -> list[models.Location]:
    """Get all solar gsps and convert dict to pydantic models."""
    gsps = await get_gsps_cached(db, auth)
    if isinstance(gsps[0], dict):
        # the cache seems to return the list of pydantic elements as list of dicts
        gsps = [models.Location(**gsp) for gsp in gsps]

    return gsps


@cache(expire=300)
async def get_gsps_cached(
    db: models.StorageClientDependency, auth: AuthDependency,
) -> list[models.Location]:
    """Get all solar gsps."""
    gsps = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.GSP,
        authdata=auth,
    )

    return gsps

@router.get(
    "/{gsp_id}/forecast",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3600/hour")
@limiter.limit("10/second")
@cache(key_builder=key_builder)
async def get_forecasts_for_a_specific_gsp(
    request: Request,  # noqa: ARG001
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
    gsps = await get_gsps(db, auth)
    filtered_gsps = [g for g in gsps if int(g.metadata["gsp_id"]) == gsp_id]
    if len(filtered_gsps) != 1:
        log.warning(f"GSP with gsp_id {gsp_id} not found")
        return []
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
        forecaster_name=GSP_FORECASTER_NAME,
        forecaster_version=GSP_FORECASTER_VERSION,
    )

    out: list[ForecastValue] = [
        ForecastValue(
            target_time=pp.valid_timestamp,
            expected_power_generation_megawatts=round(pp.power_kilowatts / 1000, 4),
            expected_power_generation_normalized=round(
                pp.power_kilowatts / pp.capacity_kilowatts if pp.capacity_kilowatts!=0 else 0,
                4,
            ),
        )
        for pp in pgvs
    ]

    return out


@router.get(
    "/{gsp_id}/pvlive",
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3600/hour")
@limiter.limit("10/second")
@cache(key_builder=key_builder)
async def get_truths_for_a_specific_gsp(
    request: Request,  # noqa: ARG001
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
    gsps = await get_gsps(db, auth)
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
    response_model=list[OneDatetimeManyForecastValuesMW | Forecast],
    include_in_schema=True,
)
@limiter.limit("3600/hour")
@limiter.limit("10/second")
@cache(key_builder=key_builder, expire=60 * 30)
async def get_all_available_forecasts(
    request: Request,  # noqa: ARG001
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_datetime_utc: Annotated[
        models.UTCDatetimeDefaultNowWindowStart,
        AfterValidator(lambda v: pd.Timestamp(v).ceil("30min").to_pydatetime()),
    ],
    end_datetime_utc: Annotated[
        dt.datetime,
        Depends(limit_end_datetime_by_permissions),
    ],
    creation_utc_limit: models.UTCDatetime | None = None,
    gsp_ids: str | None = None,
    compact: bool = False,

) -> list[OneDatetimeManyForecastValuesMW | Forecast]:
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
    gsps = await get_gsps(db, auth)
    gsp_ids = convert_list_of_gsp_ids(gsp_ids)
    gsp_uuid_id_map: dict[UUID, int] = {
        gsp.uuid: int(gsp.metadata["gsp_id"]) for gsp in gsps
        if gsp_ids is None or int(gsp.metadata["gsp_id"]) in gsp_ids
    }

    # if gsp ids are not set, then we use snapshot method, which gets all gsps for one timestamp
    # if gsp_ids is set, then we loop over all gsp ids to get forecasts. The UI current needs this.
    if gsp_ids is None:
        snapshot = await db.get_predicted_generation_snapshot(
            location_uuids=gsp_uuid_id_map.keys(),
            snapshot_timestamp_utc=start_datetime_utc,
            energy_type=models.EnergyType.SOLAR,
            forecaster_name=GSP_FORECASTER_NAME,
            forecaster_version=GSP_FORECASTER_VERSION,
            authdata={},
        )
        results = [snapshot]
    else:
        tasks = []
        for gsp_uuid in gsp_uuid_id_map:
            tasks.append(
                asyncio.create_task(
                    db.get_predicted_generation(
                        location_uuid=str(gsp_uuid),
                        window_start=start_datetime_utc,
                        window_end=end_datetime_utc,
                        energy_type=models.EnergyType.SOLAR,
                        location_type=models.LocationType.GSP,
                        authdata={},
                        created_cutoff=creation_utc_limit,
                        forecast_horizon_minutes=0,
                        forecaster_name=GSP_FORECASTER_NAME,
                        forecaster_version=GSP_FORECASTER_VERSION,
                    ),
                ),
            )

        results: list[list[models.PredictedGenerationValue] | Exception]  = await asyncio.gather(
            *tasks, return_exceptions=True,
        )
    # reorganize results by timestamp
    grouped_data: dict[dt.datetime, dict[int, float]] = defaultdict(dict)
    gsp_ids = list(gsp_uuid_id_map.values())

    if compact:
        # We can zip these because the tasks will return in the same order as they were created
        for snapshot in results:
            for predicted_generation_value in snapshot:

                gsp_id = gsp_uuid_id_map[predicted_generation_value.location_uuid]
                grouped_data[predicted_generation_value.valid_timestamp][gsp_id] \
                    = predicted_generation_value.power_kilowatts / 1000.0

        out: list[OneDatetimeManyForecastValuesMW] = [
            OneDatetimeManyForecastValuesMW(
                datetime_utc=ts,
                forecast_values=dict(sorted(gsp_dict.items())),
            )
            for ts, gsp_dict in grouped_data.items()
        ]

        return out
    else:
        # Lets format like a list of Forecasts objects

        # 1. lets split the results up into groups of gsps
        forecast_values_by_gsp_id = {}
        forecasts_by_gsp_id = {}
        for snapshot in results:
            for predicted_generation_value in snapshot:
                gsp_id = gsp_uuid_id_map[predicted_generation_value.location_uuid]
                forecast_value = ForecastValue(
                    expected_power_generation_megawatts
                        =round(predicted_generation_value.power_kilowatts / 1000, 4),
                    target_time=predicted_generation_value.valid_timestamp,
                )
                forecast_values_by_gsp_id.setdefault(gsp_id, []).append(forecast_value)

                if gsp_id not in forecasts_by_gsp_id:

                    version = predicted_generation_value.metadata.get("app_version",
                                                                      predicted_generation_value.forecaster_version)
                    input_data = format_metadata(predicted_generation_value.metadata)
                    gsp = next(g for g in gsps if int(g.metadata["gsp_id"]) == gsp_id)
                    forecast_creation_time = predicted_generation_value.created_timestamp

                    forecasts_by_gsp_id[gsp_id] = Forecast(
                    location=Location.from_location(gsp),
                    model=MLModel(
                        name=predicted_generation_value.forecaster_name,
                        version=version,
                    ),
                    forecast_creation_time=forecast_creation_time,
                    initialization_datetime_utc=predicted_generation_value.init_timestamp,
                    # we will add to this later
                    forecast_values=[],
                    input_data_last_updated=input_data,
                )


        forecasts: list[Forecast] = []
        gsp_ids = sorted(gsp_uuid_id_map.values())
        for gsp_id in gsp_ids:

            gsp_forecasts = forecasts_by_gsp_id[gsp_id]
            forecast_values = forecast_values_by_gsp_id[gsp_id]

            gsp_forecasts.forecast_values = forecast_values

            forecasts.append(gsp_forecasts)

        return forecasts







@router.get(
    "/pvlive/all",
    response_model=list[GSPYieldGroupByDatetime],
    include_in_schema=False,
)
@limiter.limit("3600/hour")
@limiter.limit("10/second")
@cache(key_builder=key_builder, expire=60 * 30)
async def get_truths_for_all_gsps(
    request: Request,  # noqa: ARG001
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
    gsps = await get_gsps(db, auth)
    gsp_ids = convert_list_of_gsp_ids(gsp_ids)
    gsp_uuid_id_map: dict[UUID, int] = {
        gsp.uuid: int(gsp.metadata["gsp_id"]) for gsp in gsps
        if gsp_ids is None or int(gsp.metadata["gsp_id"]) in gsp_ids
    }

    out: list[GSPYieldGroupByDatetime] = []

    if gsp_ids is None:
        # Return a snapshot of the data at the start_datetime_utc for all gsps
        values = await db.get_actual_generation_snapshot(
                location_uuids=list(gsp_uuid_id_map.keys()),
                snapshot_timestamp_utc=start_datetime_utc,
                energy_type=models.EnergyType.SOLAR,
                observer_name=f"pvlive_{regime}",
                authdata={},
            )
        out = [
            GSPYieldGroupByDatetime(
                datetime_utc=start_datetime_utc,
                generation_kw_by_gsp_id={
                    gsp_uuid_id_map[v.location_uuid]: v.power_kilowatts for v in values
                },
            ),
        ]

    else:
        # If specific GSP IDs are set, then return a 2d response of all timestamps for each GSP.
        # Looping over snapshots results in fewer calls than looping over GSPs
        tasks = []
        for ts in pd.date_range(start=start_datetime_utc, end=end_datetime_utc, freq="30min"):
            tasks.append(
                asyncio.create_task(
                    db.get_actual_generation_snapshot(
                        location_uuids=list(gsp_uuid_id_map.keys()),
                        snapshot_timestamp_utc=ts,
                        energy_type=models.EnergyType.SOLAR,
                        observer_name=f"pvlive_{regime}",
                        authdata={},
                    ),
                ),
            )

        results: list[list[models.ActualGenerationValue] | Exception]  = await asyncio.gather(
            *tasks, return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                raise result

            if isinstance(result, list) and len(result) > 0:
                out.append(
                    GSPYieldGroupByDatetime(
                        datetime_utc=result[0].valid_timestamp,
                        generation_kw_by_gsp_id={
                            gsp_uuid_id_map[v.location_uuid]: v.power_kilowatts for v in result
                        },
                    ),
                )

    return out


