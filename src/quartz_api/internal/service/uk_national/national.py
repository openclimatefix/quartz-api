"""The 'national' FastAPI router object."""

from enum import Enum

from fastapi import APIRouter, Request
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    DBClientDependency,
)

from .pydantic_models import NationalForecast, NationalForecastValue, NationalYield

router = APIRouter(tags=["National"])

model_names_external_to_internal = {
    "blend": "blend",
    "pvnet_intraday": "pvnet_v2",
    "pvnet_day_ahead": "pvnet_day_ahead",
    "pvnet_intraday_ecmwf_only": "pvnet_ecmwf",
    "pvnet_intraday_met_office_only": "pvnet-ukv-only",
    "pvnet_intraday_sat_only": "pvnet-sat-only",
}


class ModelName(str, Enum):
    """Available model options for national forecasts."""

    blend = "blend"
    pvnet_intraday = "pvnet_intraday"
    pvnet_day_ahead = "pvnet_day_ahead"
    pvnet_intraday_ecmwf_only = "pvnet_intraday_ecmwf_only"
    pvnet_intraday_met_office_only = "pvnet_intraday_met_office_only"
    pvnet_intraday_sat_only = "pvnet_intraday_sat_only"


@router.get(
    "/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_national_forecast(
    db: DBClientDependency,
    auth: AuthDependency,
    request: Request,
    forecast_horizon_minutes: int | None = None,
    include_metadata: bool = False,
    start_datetime_utc: str | None = None,
    end_datetime_utc: str | None = None,
    creation_limit_utc: str | None = None,
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
    raise NotImplementedError()


@router.get(
    "/pvlive",
    status_code=status.HTTP_200_OK,
)
async def get_national_pvlive(
    db: DBClientDependency,
    auth: AuthDependency,
    regime: str | None = "in-day",
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
    raise NotImplementedError()


# Note have removed elexon API call, as not used
