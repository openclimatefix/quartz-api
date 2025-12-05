"""The 'regions' router object and associated routes logic."""

# ruff: noqa: ARG001
import datetime as dt
import pathlib
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Path
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ._csv import format_csv_and_created_time
from ._resample import resample_generation

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

class GetSourcesResponse(BaseModel):
    """Model for the sources endpoint response."""

    sources: list[str]


@router.get(
    "/sources",
    status_code=status.HTTP_200_OK,
)
async def get_sources_route(
    auth: AuthDependency,
) -> GetSourcesResponse:
    """Get available generation sources."""
    return GetSourcesResponse(sources=["wind", "solar"])


class GetRegionsResponse(BaseModel):
    """Model for the regions endpoint response."""

    regions: list[str]

ValidSource = Annotated[str, Path(
    description="The source of the generation or forecast data.",
    pattern="^(wind|solar)$",
)]

@router.get(
    "/{source}/regions",
    status_code=status.HTTP_200_OK,
)
async def get_regions_route(
    source: ValidSource,
    db: models.DBClientDependency,
    auth: AuthDependency,
    # TODO: add auth scopes
) -> GetRegionsResponse:
    """Get available regions for a given source."""
    if source == "wind":
        regions = await db.get_wind_regions()
    elif source == "solar":
        regions = await db.get_solar_regions()
    return GetRegionsResponse(regions=regions)


class GetHistoricGenerationResponse(BaseModel):
    """Model for the historic generation endpoint response."""

    values: list[models.ActualPower]


@router.get(
    "/{source}/{region}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_historic_timeseries_route(
    source: ValidSource,
    region: str,
    db: models.DBClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
    # TODO: add auth scopes
    resample_minutes: int | None = None,
) -> GetHistoricGenerationResponse:
    """Get observed generation as a timeseries for a given source and region."""
    values: list[models.ActualPower] = []

    try:
        if source == "wind":
            values = await db.get_actual_wind_power_production_for_location(location=region)
        elif source == "solar":
            values = await db.get_actual_solar_power_production_for_location(location=region)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting {source} power production: {e}",
        ) from e

    if resample_minutes is not None:
        values = resample_generation(values=values, interval_minutes=resample_minutes)

    return GetHistoricGenerationResponse(
        values=[
            y.to_timezone(tz=tz)
            for y in values
            if y.Time < dt.datetime.now(tz=dt.UTC)
        ],
    )


class GetForecastGenerationResponse(BaseModel):
    """Model for the forecast generation endpoint response."""

    values: list[models.PredictedPower]


@router.get(
    "/{source}/{region}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast_timeseries_route(
    source: ValidSource,
    region: str,
    db: models.DBClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
    # TODO: add auth scopes
    forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.day_ahead,
    forecast_horizon_minutes: int | None = None,
    smooth_flag: bool = True,
) -> GetForecastGenerationResponse:
    """Get forecasted generation as a timeseries for a given source and region.

    The smooth_flag indicates whether to return smoothed forecasts or not.
    Note that for Day Ahead forecasts, smoothing is never applied.
    """
    values: list[models.PredictedPower] = []

    if forecast_horizon == models.ForecastHorizon.day_ahead:
        smooth_flag = False

    try:
        if source == "wind":
            values = await db.get_predicted_wind_power_production_for_location(
                location=region,
                forecast_horizon=forecast_horizon,
                forecast_horizon_minutes=forecast_horizon_minutes,
                smooth_flag=smooth_flag,
            )
        elif source == "solar":
            values = await db.get_predicted_solar_power_production_for_location(
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
        values=[y.to_timezone(tz=tz) for y in values],
    )


@router.get(
    "/{source}/{region}/forecast/csv",
    response_class=FileResponse,
)
async def get_forecast_csv(
    source: ValidSource,
    region: str,
    db: models.DBClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
    forecast_horizon: models.ForecastHorizon = models.ForecastHorizon.latest,
) -> StreamingResponse:
    """Route to get the day ahead forecast as a CSV file.

    By default, the CSV file will be for the latest forecast, from now forwards.
    The forecast_horizon can be set to 'latest' or 'day_ahead'.
    - latest: The latest forecast, from now forwards.
    - day_ahead: The forecast for the next day, from 00:00.
    """
    if forecast_horizon is not None and forecast_horizon not in [
        models.ForecastHorizon.latest,
        models.ForecastHorizon.day_ahead,
    ]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid forecast_horizon {forecast_horizon}. Must be 'latest' or 'day_ahead'.",
        )

    forecasts: GetForecastGenerationResponse = await get_forecast_timeseries_route(
        source=source,
        region=region,
        db=db,
        auth=auth,
        forecast_horizon=forecast_horizon,
        smooth_flag=False,
        tz=tz,
    )

    # format to dataframe
    df, created_time = format_csv_and_created_time(
        forecasts.values,
        forecast_horizon=forecast_horizon,
    )

    # make file format
    # NOTE: See note above format_csv_and_created_time about timezones
    now_ist = pd.Timestamp.now(tz="Asia/Kolkata")
    tomorrow_ist = df["Date [IST]"].iloc[0]
    match forecast_horizon:
        case models.ForecastHorizon.latest:
            forecast_type = "intraday"
        case models.ForecastHorizon.day_ahead:
            forecast_type = "da"
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid forecast_horizon {forecast_horizon}. "
                "Must be 'latest' or 'day_ahead'.",
            )
    csv_file_path = f"{region}_{source}_{forecast_type}_{tomorrow_ist}.csv"

    description = (
        f"Forecast for {region} for {source}, {forecast_type}, for {tomorrow_ist}. "
        f"The Forecast was created at {created_time} and downloaded at {now_ist}"
    )

    output = df.to_csv(index=False)
    return StreamingResponse(
        iter([output, description]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment;filename={csv_file_path}"},
    )

