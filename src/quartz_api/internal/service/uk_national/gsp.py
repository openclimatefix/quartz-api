"""The 'gsp' FastAPI router object."""

from datetime import UTC, datetime

from fastapi import APIRouter
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    DBClientDependency,
    ForecastHorizon,
    GSPYieldGroupByDatetime,
    OneDatetimeManyForecastValuesMW,
)

from .cache import key_builder
from .pydantic_models import ForecastValue, GSPYield
from .time_utils import (
    ceil_30_minutes_dt,
    floor_30_minutes_dt,
    format_datetime,
    limit_end_datetime_by_permissions,
)

router = APIRouter(tags=["GSP"])


@router.get(
    "/{gsp_id}/forecast",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_forecasts_for_a_specific_gsp(
    db: DBClientDependency,
    auth: AuthDependency,
    gsp_id: int,
    forecast_horizon_minutes: int | None = None,
    start_datetime_utc: str | None = None,
    end_datetime_utc: str | None = None,
    creation_utc_limit: str | None = None,
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
    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)
    creation_utc_limit = format_datetime(creation_utc_limit)

    permissions = getattr(auth, "permissions", [])
    end_datetime_utc = limit_end_datetime_by_permissions(permissions, end_datetime_utc)

    gsps = await db.get_solar_regions(type="gsp")
    gsp_location = [
        site for site in gsps if int(site.region_metadata["gsp_id"]) == gsp_id
    ]
    gsp_location_uuid = str(gsp_location[0].region_metadata["location_uuid"])

    forecast_horizon = ForecastHorizon.latest
    if forecast_horizon_minutes is None:
        forecast_horizon = ForecastHorizon.horizon

    predicted_powers = await db.get_predicted_solar_power_production_for_location(
        location=gsp_location_uuid,
        forecast_horizon=forecast_horizon,
        forecast_horizon_minutes=forecast_horizon_minutes,
        smooth_flag=False,
        model_name="blend",
        start_datetime=start_datetime_utc,
        end_datetime=end_datetime_utc,
        created_utc_upper_limit=creation_utc_limit,
    )

    national_forecasts = [
        ForecastValue(
            target_time=pp.Time,
            expected_power_generation_megawatts=pp.PowerKW / 1000,
        )
        for pp in predicted_powers
    ]

    return national_forecasts


@router.get(
    "/{gsp_id}/pvlive",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_truths_for_a_specific_gsp(
    db: DBClientDependency,
    auth: AuthDependency,  # noqa FBT001 # TODO
    gsp_id: int,
    regime: str = "in-day",
    start_datetime_utc: str | None = None,
    end_datetime_utc: str | None = None,
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
    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)

    gsps = await db.get_solar_regions(type="gsp")

    gsp_location = [
        site for site in gsps if int(site.region_metadata["gsp_id"]) == gsp_id
    ]

    gsp_location_uuid = str(gsp_location[0].region_metadata["location_uuid"])

    regime = regime.replace("-", "_")

    solar_production = await db.get_actual_solar_power_production_for_location(
        location=gsp_location_uuid,
        observer_name=f"pvlive_{regime}",
        start_datetime=start_datetime_utc,
        end_datetime=end_datetime_utc,
    )

    gsp_yields = [
        GSPYield(
            datetime_utc=sp.Time,
            solar_generation_kw=sp.PowerKW,
        )
        for sp in solar_production
    ]

    return gsp_yields


# corresponds to route /v0/solar/GB/gsp/forecast/all/
# TODO currently takes 9 seconds to load, so probably needs optimization
@router.get(
    "/forecast/all/",
    response_model=list[OneDatetimeManyForecastValuesMW],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=60*30)
async def get_all_available_forecasts(
    db: DBClientDependency,
    auth: AuthDependency,
    start_datetime_utc: str | None = None,
    end_datetime_utc: str | None = None,
    gsp_ids: str | None = None,
    creation_limit_utc: str | None = None,
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
    - **end_datetime_utc**: optional end datetime for the query. e.g '2023-08-12 14:00:00+00:00'
    """
    gsps = await db.get_solar_regions(type="gsp")
    # might need to add nation location in here too

    # format gsp_ids
    if isinstance(gsp_ids, str):
        gsp_ids = [int(gsp_id) for gsp_id in gsp_ids.split(",") if gsp_id != ""]
        if len(gsp_ids) == 0:
            gsp_ids = None

    # get locations uuids
    location_uuids_to_gsp_id = {
        str(gsp.region_metadata["location_uuid"]): int(gsp.region_metadata["gsp_id"])
        for gsp in gsps
    }
    if gsp_ids is not None:
        location_uuids_to_gsp_id = {
            location_uuid: gsp_id
            for location_uuid, gsp_id in location_uuids_to_gsp_id.items()
            if gsp_id in gsp_ids
        }

    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)
    creation_limit_utc = format_datetime(creation_limit_utc)

    if start_datetime_utc is not None:
        start_datetime_utc = ceil_30_minutes_dt(start_datetime_utc)
    if end_datetime_utc is not None:
        end_datetime_utc = floor_30_minutes_dt(end_datetime_utc)

    # TODO
    # end_datetime_utc = limit_end_datetime_by_permissions(permissions, end_datetime_utc)

    # by default, don't get any data in the past if more than one gsp
    if start_datetime_utc is None and (gsp_ids is None or len(gsp_ids) > 1):
        start_datetime_utc = floor_30_minutes_dt(datetime.now(tz=UTC))

    if start_datetime_utc is not None:
        start_datetime_utc = ceil_30_minutes_dt(start_datetime_utc)

    forecast_values = await db.get_forecast_for_multiple_locations(
        location_uuids_to_location_ids=location_uuids_to_gsp_id,
        start_datetime_utc=start_datetime_utc,
        end_datetime_utc=end_datetime_utc,
        model_name="blend",
        authdata=auth,
    )

    return forecast_values


# corresponds to API route /v0/solar/GB/gsp/pvlive/all
# TODO currently takes 2 seconds to load, so probably needs optimization
@router.get(
    "/pvlive/all",
    response_model=list[GSPYieldGroupByDatetime],
    include_in_schema=False,
)
@cache(key_builder=key_builder, expire=60*30)
async def get_truths_for_all_gsps(
    db: DBClientDependency,
    auth: AuthDependency,
    regime: str = "in-day",
    start_datetime_utc: str | None = None,
    end_datetime_utc: str | None = None,
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
    if isinstance(gsp_ids, str):
        gsp_ids = [int(gsp_id) for gsp_id in gsp_ids.split(",") if gsp_id != ""]

    gsps = await db.get_solar_regions(type="gsp")

    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)

    # get locations uuids
    location_uuids_to_gsp_id = {
        str(gsp.region_metadata["location_uuid"]): int(gsp.region_metadata["gsp_id"])
        for gsp in gsps
    }
    if gsp_ids is not None:
        location_uuids_to_gsp_id = {
            location_uuid: gsp_id
            for location_uuid, gsp_id in location_uuids_to_gsp_id.items()
            if gsp_id in gsp_ids
        }

    observations = await db.get_generation_for_multiple_locations(
        location_uuids_to_location_ids=location_uuids_to_gsp_id,
        observer_name=f"pvlive_{regime.replace('-', '_')}",
        start_datetime=start_datetime_utc,
        end_datetime=end_datetime_utc,
        authdata=auth,
    )

    return observations
