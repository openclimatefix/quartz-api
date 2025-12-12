"""The 'gsp' FastAPI router object."""

from fastapi import APIRouter
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    DBClientDependency,
    ForecastHorizon,
)

from .pydantic_models import ForecastValue, GSPYield
from .time_utils import format_datetime

router = APIRouter(tags=["GSP"])


@router.get(
    "/{gsp_id}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecasts_for_a_specific_gsp(
    db: DBClientDependency,
    auth: AuthDependency, # noqa FBT001 # TODO
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
    if creation_utc_limit is not None:
        raise NotImplementedError()

    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)

    gsps = await db.get_solar_regions(type="gsp")
    gsp_location = [
        site for site in gsps if int(site.region_metadata["gsp_id"].number_value) == gsp_id
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
    )

    national_forecasts = [ForecastValue(
                target_time=pp.Time,
                expected_power_generation_megawatts=pp.PowerKW/1000,
            ) for pp in predicted_powers]

    return national_forecasts


@router.get(
    "/{gsp_id}/pvlive",
    status_code=status.HTTP_200_OK,
)
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
        site for site in gsps if int(site.region_metadata["gsp_id"].number_value) == gsp_id
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


# TODO add forecast/all and pvlive/all route.
# These are hidden but used by the UI
