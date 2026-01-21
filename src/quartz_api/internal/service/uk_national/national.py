"""The 'national' FastAPI router object."""

from datetime import UTC, datetime
from enum import Enum

from fastapi import APIRouter
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.models import (
    DBClientDependency,
    ForecastHorizon,
)

from .cache import key_builder
from .pydantic_models import (
    InputDataLastUpdated,
    Location,
    MLModel,
    NationalForecast,
    NationalForecastValue,
    NationalYield,
)
from .time_utils import format_datetime, get_window, limit_end_datetime_by_permissions

router = APIRouter(tags=["National"])

model_names_external_to_internal = {
    "blend": "blend",
    "pvnet_intraday": "pvnet_v2",
    "pvnet_day_ahead": "pvnet_day_ahead",
    "pvnet_intraday_ecmwf_only": "pvnet_ecmwf",
    "pvnet_intraday_met_office_only": "pvnet_ukv_only",
    "pvnet_intraday_sat_only": "pvnet_sat_only",
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
@cache(key_builder=key_builder)
async def get_national_forecast(
    db: DBClientDependency,
    auth: AuthDependency,
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
    start_datetime_utc = format_datetime(start_datetime_utc)
    end_datetime_utc = format_datetime(end_datetime_utc)
    creation_limit_utc = format_datetime(creation_limit_utc)

    permissions = getattr(auth, "permissions", [])
    end_datetime_utc = limit_end_datetime_by_permissions(permissions, end_datetime_utc)

    start_datetime_utc, end_datetime_utc = get_window(start=start_datetime_utc,
                                                      end=end_datetime_utc)

    model_name = model_names_external_to_internal[model_name]
    if trend_adjuster_on:
        model_name = model_name + "_adjust"

    sites = await db.get_solar_regions(type="nation")
    national_location_uuid = sites[0].region_metadata["location_uuid"]

    forecast_horizon = ForecastHorizon.latest
    if forecast_horizon_minutes is not None:
        forecast_horizon = ForecastHorizon.horizon

    predicted_powers = await db.get_predicted_solar_power_production_for_location(
        location=national_location_uuid,
        forecast_horizon=forecast_horizon,
        forecast_horizon_minutes=forecast_horizon_minutes,
        smooth_flag=False,
        forecaster_name=model_name,
        start_datetime=start_datetime_utc,
        end_datetime=end_datetime_utc,
        created_before_datetime=creation_limit_utc,
    )


    national_forecast_values = [
            NationalForecastValue(
                target_time=pp.time,
                expected_power_generation_megawatts=pp.power_kW / 1000,
                plevels={
                    "plevel_10": pp.plevel_kW["p10"] / 1000,
                    "plevel_90": pp.plevel_kW["p90"] / 1000,
                },
            )
            for pp in predicted_powers
        ]

    if not include_metadata:
        return national_forecast_values
    else:

        #  Legacy inputdata,
        # In nowcasting_datamodel, we get this from the database
        old = datetime(1970, 1, 1, tzinfo=UTC)
        input = InputDataLastUpdated(gsp=old, nwp=old, pv=old, satellite=old)

        national_forecast = NationalForecast(
            location=Location.from_region(sites[0]),
            model=MLModel(name=predicted_powers[0].forecaster_name,
                          version=predicted_powers[0].forecaster_version),
            forecast_creation_time=predicted_powers[0].created_time,
            initialization_datetime_utc=predicted_powers[0].created_time,
            forecast_values=national_forecast_values,
            input_data_last_updated=input)

        return national_forecast






@router.get(
    "/pvlive",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder)
async def get_national_pvlive(
    db: DBClientDependency,
    auth: AuthDependency,  # noqa FBT001
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
    sites = await db.get_solar_regions(type="nation")
    national_location_uuid = sites[0].region_metadata["location_uuid"]

    regime = regime.replace("-", "_")

    start_datetime_utc, end_datetime_utc = get_window()

    solar_production = await db.get_actual_solar_power_production_for_location(
        location=national_location_uuid,
        observer_name=f"pvlive_{regime}",
        start_datetime=start_datetime_utc,
        end_datetime=end_datetime_utc,
    )

    national_yields = [
        NationalYield(
            datetime_utc=sp.Time,
            solar_generation_kw=sp.PowerKW,
        )
        for sp in solar_production
    ]

    return national_yields


# Note have removed elexon API call, as not used
