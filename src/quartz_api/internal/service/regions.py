import datetime as dt
from typing import Optional, Annotated

import pandas as pd
from fastapi import APIRouter
from fastapi import Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette import status
from starlette.requests import Request

from quartz_api.internal import ActualPower, PredictedPower
from quartz_api.internal.models import ForecastHorizon
from quartz_api.internal.service.auth import auth
from quartz_api.internal.service.constants import local_tz
from quartz_api.internal.service.csv import format_csv_and_created_time
from quartz_api.internal.service.database_client import DBClientDependency
from quartz_api.internal.service.resample import resample_generation

router = APIRouter(
    tags=["Regions"],
)


class GetSourcesResponse(BaseModel):
    """Model for the sources endpoint response."""

    sources: list[str]


@router.get(
    "/sources",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_sources_route(auth: dict = Depends(auth)) -> GetSourcesResponse:
    """Function for the sources route."""

    return GetSourcesResponse(sources=["wind", "solar"])


class GetRegionsResponse(BaseModel):
    """Model for the regions endpoint response."""

    regions: list[str]


def validate_source(source: str) -> str:
    """Validate the source parameter."""
    if source not in ["wind", "solar"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown source {source}; valid sources are 'wind' and 'solar'.",
        )
    return source


ValidSourceDependency = Annotated[str, Depends(validate_source)]


@router.get(
    "/{source}/regions",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_regions_route(
    source: ValidSourceDependency,
    db: DBClientDependency,
    auth: dict = Depends(auth),
    # TODO: add auth scopes
) -> GetRegionsResponse:
    """Function for the regions route."""

    if source == "wind":
        regions = db.get_wind_regions()
    elif source == "solar":
        regions = db.get_solar_regions()
    return GetRegionsResponse(regions=regions)


class GetHistoricGenerationResponse(BaseModel):
    """Model for the historic generation endpoint response."""

    values: list[ActualPower]


@router.get(
    "/{source}/{region}/generation",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_historic_timeseries_route(
    source: ValidSourceDependency,
    request: Request,
    region: str,
    db: DBClientDependency,
    auth: dict = Depends(auth),
    # TODO: add auth scopes
    resample_minutes: Optional[int] = None,
) -> GetHistoricGenerationResponse:
    """Function for the historic generation route."""
    values: list[ActualPower] = []

    try:
        if source == "wind":
            values = db.get_actual_wind_power_production_for_location(location=region)
        elif source == "solar":
            values = db.get_actual_solar_power_production_for_location(location=region)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting {source} power production: {e}",
        ) from e

    if resample_minutes is not None:
        values = resample_generation(values=values, internal_minutes=resample_minutes)

    return GetHistoricGenerationResponse(
        values=[y.to_timezone(tz=local_tz) for y in values if y.Time < dt.datetime.now(tz=dt.UTC)],
    )


class GetForecastGenerationResponse(BaseModel):
    """Model for the forecast generation endpoint response."""

    values: list[PredictedPower]


@router.get(
    "/{source}/{region}/forecast",
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_forecast_timeseries_route(
    source: ValidSourceDependency,
    region: str,
    db: DBClientDependency,
    auth: dict = Depends(auth),
    # TODO: add auth scopes
    forecast_horizon: ForecastHorizon = ForecastHorizon.day_ahead,
    forecast_horizon_minutes: Optional[int] = None,
    smooth_flag: bool = True,
) -> GetForecastGenerationResponse:
    """Function for the forecast generation route.

    Args:
        source: The source of the forecast, this is current wind or solar.
        region: The region to get the forecast for.
        forecast_horizon: The time horizon to get the data for. Can be 'latest', 'horizon' or 'day ahead'
        forecast_horizon_minutes: The number of minutes to get the forecast for. forecast_horizon must be 'horizon'
        smooth_flag: If the forecast should be smoothed or not. Note for DA forecast this is always False.
    """
    values: list[PredictedPower] = []

    if forecast_horizon == ForecastHorizon.day_ahead:
        smooth_flag = False

    try:
        if source == "wind":
            values = db.get_predicted_wind_power_production_for_location(
                location=region,
                forecast_horizon=forecast_horizon,
                forecast_horizon_minutes=forecast_horizon_minutes,
                smooth_flag=smooth_flag,
            )
        elif source == "solar":
            values = db.get_predicted_solar_power_production_for_location(
                location=region,
                forecast_horizon=forecast_horizon,
                forecast_horizon_minutes=forecast_horizon_minutes,
                smooth_flag=smooth_flag,
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting {source} power production: {e}",
        ) from e

    return GetForecastGenerationResponse(
        values=[y.to_timezone(tz=local_tz) for y in values],
    )


@router.get(
    "/{source}/{region}/forecast/csv",
    response_class=FileResponse,
    include_in_schema=False,
)
def get_forecast_csv(
    source: ValidSourceDependency,
    region: str,
    db: DBClientDependency,
    auth: dict = Depends(auth),
    forecast_horizon: Optional[ForecastHorizon] = ForecastHorizon.latest,
):
    """
    Route to get the day ahead forecast as a CSV file.
    By default, the CSV file will be for the latest forecast, from now forwards.
    The forecast_horizon can be set to 'latest' or 'day_ahead'.
    - latest: The latest forecast, from now forwards.
    - day_ahead: The forecast for the next day, from 00:00.
    """

    if forecast_horizon is not None:
        if forecast_horizon not in [ForecastHorizon.latest, ForecastHorizon.day_ahead]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid forecast_horizon {forecast_horizon}. Must be 'latest' or 'day_ahead'.",
            )

    forecasts: GetForecastGenerationResponse = get_forecast_timeseries_route(
        source=source,
        region=region,
        db=db,
        auth=auth,
        forecast_horizon=forecast_horizon,
        smooth_flag=False,
    )

    # format to dataframe
    df, created_time = format_csv_and_created_time(forecasts.values, forecast_horizon=forecast_horizon)

    # make file format
    now_ist = pd.Timestamp.now(tz="Asia/Kolkata")
    tomorrow_ist = df["Date [IST]"].iloc[0]
    match forecast_horizon:
        case ForecastHorizon.latest:
            forecast_type = "intraday"
        case ForecastHorizon.day_ahead:
            forecast_type = "da"
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid forecast_horizon {forecast_horizon}. Must be 'latest' or 'day_ahead'.",
            )
    csv_file_path = f"{region}_{source}_{forecast_type}_{tomorrow_ist}.csv"

    description = (
        f"Forecast for {region} for {source}, {forecast_type}, for {tomorrow_ist}. "
        f"The Forecast was created at {created_time} and downloaded at {now_ist}"
    )

    output = df.to_csv(index=False)
    return StreamingResponse(
        iter([output] + [description]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment;filename={csv_file_path}"},
    )
