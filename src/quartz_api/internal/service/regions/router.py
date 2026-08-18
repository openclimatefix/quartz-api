"""The 'regions' router object and associated routes logic."""

# ruff: noqa: ARG001
import datetime as dt
import pathlib

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ._csv import format_csv_and_created_time
from ._resample import resample_generation, smooth_forecast
from .endpoint_types import (
    ActualPower,
    GetForecastGenerationResponse,
    GetHistoricGenerationResponse,
    GetRegionsResponse,
    GetSourcesResponse,
    PredictedPower,
    ValidSource,
)

router = APIRouter(tags=[pathlib.Path(__file__).parent.stem.capitalize()])

# Backwards-compatibility aliases. The old API exposed a single "ruvnl" region;
# it has since been split into source-specific regions, so map the legacy name
# onto the new one per source before resolving.
_REGION_ALIASES: dict[str, dict[str, str]] = {
    "solar": {"ruvnl": "ruvnl_solar"},
    "wind": {"ruvnl": "ruvnl_wind"},
}

_REGION_OBSERVER_NAME = "ruvnl"

# Pinned per source, so every horizon of a chart is served by the same model.
_REGION_FORECASTER_NAMES: dict[str, str] = {
    "solar": "pvnet_india",
    "wind": "windnet_india_mo_v2",
}


async def _resolve_region_location(
    db: models.StorageInterface,
    source: str,
    region: str,
) -> models.Location:
    """Resolve a region name to its Location, raising 404 if it doesn't exist."""
    region = _REGION_ALIASES.get(source, {}).get(region, region)
    locations = await db.get_locations(
        energy_type=(
            models.EnergyType.WIND if source == "wind" else models.EnergyType.SOLAR
        ),
        location_type=models.LocationType.REGION,
        authdata={},
    )
    location = next((loc for loc in locations if loc.name == region), None)
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{region}' not found.",
        )
    return location


@router.get(
    "/sources",
    status_code=status.HTTP_200_OK,
)
async def get_sources_route(
    auth: AuthDependency,
) -> GetSourcesResponse:
    """Get available generation sources."""
    return GetSourcesResponse(
        sources=[
            "wind",
            "solar",
        ],
    )


@router.get(
    "/{source}/regions",
    status_code=status.HTTP_200_OK,
)
async def get_regions_route(
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    # TODO: add auth scopes
) -> GetRegionsResponse:
    """Get available regions for a given source."""
    if source == "wind":
        regions = await db.get_locations(
            energy_type=models.EnergyType.WIND,
            location_type=models.LocationType.REGION,
            authdata=auth,
        )
    elif source == "solar":
        regions = await db.get_locations(
            energy_type=models.EnergyType.SOLAR,
            location_type=models.LocationType.REGION,
            authdata={},
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid source {source}. Available sources: 'solar'.",
        )
    legacy_by_new = {new: legacy for legacy, new in _REGION_ALIASES.get(source, {}).items()}
    region_names = list(
        dict.fromkeys(legacy_by_new.get(r.name, r.name) for r in regions),
    )
    return GetRegionsResponse(regions=region_names)


@router.get(
    "/{source}/{region}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_historic_timeseries_route(
    source: ValidSource,
    region: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    tz: models.TZDependency,
    # TODO: add auth scopes
    resample_minutes: int | None = None,
) -> GetHistoricGenerationResponse:
    """Get observed generation as a timeseries for a given source and region."""
    values: list[ActualPower] = []

    location = await _resolve_region_location(db, source, region)

    try:
        agvs = await db.get_actual_generation(
            location_uuid=location.uuid,
            energy_type=(
                models.EnergyType.WIND if source == "wind" else models.EnergyType.SOLAR
            ),
            location_type=models.LocationType.REGION,
            window_start=(pd.Timestamp.utcnow() - dt.timedelta(days=2))
            .floor("D")
            .to_pydatetime(),
            window_end=pd.Timestamp.utcnow(),
            authdata={},
            observer_name=_REGION_OBSERVER_NAME,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting {source} power production: {e}",
        ) from e

    values: list[ActualPower] = [
        ActualPower(
            PowerKW=agv.power_kilowatts,
            Time=agv.valid_timestamp.astimezone(tz=tz),
            location_uuid=str(agv.location_uuid),
        )
        for agv in agvs
    ]

    if resample_minutes is not None:
        values = resample_generation(values=values, interval_minutes=resample_minutes)

    return GetHistoricGenerationResponse(values=values)


@router.get(
    "/{source}/{region}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast_timeseries_route(
    source: ValidSource,
    region: str,
    db: models.StorageClientDependency,
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
    values: list[PredictedPower] = []
    day_ahead_forecast = False
    day_ahead_closure_time_local = None

    location = await _resolve_region_location(db, source, region)

    match forecast_horizon, forecast_horizon_minutes:
        case models.ForecastHorizon.latest, _:
            horizon_mins: int = 0
        case models.ForecastHorizon.day_ahead, None:
            horizon_mins = 0
            day_ahead_forecast = True
            day_ahead_closure_time_local = dt.time(9, 0)
            smooth_flag = False
        case models.ForecastHorizon.horizon, int():
            horizon_mins = forecast_horizon_minutes
        case _:
            horizon_mins = 0

    try:
        pgvs = await db.get_predicted_generation(
            location_uuid=location.uuid,
            window_start=(pd.Timestamp.utcnow() - dt.timedelta(days=2))
            .floor("D")
            .to_pydatetime(),
            window_end=pd.Timestamp.utcnow().floor("h").to_pydatetime()
            + dt.timedelta(days=2),
            energy_type=(
                models.EnergyType.WIND if source == "wind" else models.EnergyType.SOLAR
            ),
            location_type=models.LocationType.REGION,
            forecast_horizon_minutes=horizon_mins,
            authdata={},
            forecaster_name=_REGION_FORECASTER_NAMES[source],
            day_ahead=day_ahead_forecast,
            day_ahead_closure_time_local=day_ahead_closure_time_local,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting {source} power production: {e}",
        ) from e

    values: list[PredictedPower] = [
        PredictedPower(
            location_uuid=str(pgv.location_uuid),
            PowerKW=pgv.power_kilowatts,
            Time=pgv.valid_timestamp.astimezone(tz=tz),
            created_time=pgv.created_timestamp.astimezone(tz=tz),
            forecaster_version=pgv.forecaster_version,
            forecaster_name=pgv.forecaster_name,
        )
        for pgv in pgvs
    ]

    if smooth_flag:
        values = smooth_forecast(values=values)

    return GetForecastGenerationResponse(values=values)


@router.get(
    "/{source}/{region}/forecast/csv",
    response_class=FileResponse,
)
async def get_forecast_csv(
    source: ValidSource,
    region: str,
    db: models.StorageClientDependency,
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
    match forecast_horizon:
        case models.ForecastHorizon.latest:
            horizon_mins: int = 0
            forecast_type: str = "intraday"
        case models.ForecastHorizon.day_ahead:
            horizon_mins = 60 * 24
            forecast_type = "da"
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid forecast_horizon. Must be 'latest' or 'day_ahead'.",
            )

    location = await _resolve_region_location(db, source, region)

    pgvs = await db.get_predicted_generation(
        location_uuid=location.uuid,
        window_start=(pd.Timestamp.utcnow() - dt.timedelta(days=2))
        .floor("D")
        .to_pydatetime(),
        window_end=pd.Timestamp.utcnow().floor("h").to_pydatetime()
        + dt.timedelta(days=2),
        energy_type=(
            models.EnergyType.WIND if source == "wind" else models.EnergyType.SOLAR
        ),
        location_type=models.LocationType.REGION,
        forecast_horizon_minutes=horizon_mins,
        authdata={},
        forecaster_name=_REGION_FORECASTER_NAMES[source],
    )

    # Format to dataframe
    df, created_time = format_csv_and_created_time(
        values=pgvs,
        forecast_horizon=forecast_horizon,
        tz=tz,
    )

    # Make file format
    now = dt.datetime.now(tz=tz)
    # Name the file after the first forecast date in the CSV, as the old API did
    # (today for intraday, tomorrow for day-ahead); fall back to tomorrow when
    # there is no data.
    file_date = (
        df["Date [IST]"].iloc[0]
        if len(df) > 0
        else (now + dt.timedelta(days=1)).date()
    )
    csv_file_path = f"{region}_{source}_{forecast_type}_{file_date}.csv"
    description = (
        f"Forecast for {region} for {source}, {forecast_type}, for {file_date}. "
        f"The Forecast was created at {created_time} and downloaded at {now}"
    )

    output = df.to_csv(index=False)
    return StreamingResponse(
        iter([output, description]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment;filename={csv_file_path}"},
    )
