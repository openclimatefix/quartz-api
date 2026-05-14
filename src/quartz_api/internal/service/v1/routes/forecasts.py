"""Forecast routes — per-region, snapshot, and period matrix endpoints."""

# ruff: noqa: ARG001, B008

import asyncio
import datetime as dt
import json

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..cache import _forecast_cache_warming, _warm_v1_forecast_cache, key_builder
from ..endpoint_types import (
    CountryParam,
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    RegionForecast,
    RegionForecastMatrix,
    RegionForecastValue,
    ValidForecastModel,
    ValidPeriodRegionType,
    ValidRegion,
    ValidRegionType,
    ValidSource,
    ValidWindowStart,
)
from ..helpers import (
    _check_country_access,
    _internal_to_api_name,
    _location_display_name,
    _resolve_forecast_model,
    _resolve_nation,
    _resolve_region_id,
    _timeseries_window,
    _to_uuid,
    _validate_model,
)

router = APIRouter(tags=["Forecasts"])


@router.get(
    "/{country}/{source}/regions/{region}/forecast",
    status_code=status.HTTP_200_OK,
    response_model=ForecastResponse,
)
@cache(key_builder=key_builder, expire=60)
async def get_forecast(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    region: ValidRegion,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: dt.datetime | None = Query(
        None,
        description="End of forecast window (UTC). Defaults to 48 hours from now.",
    ),
    creation_limit_utc: dt.datetime | None = Query(
        None,
        description=(
            "Only include forecasts created at or before this time (UTC). "
            "Use to retrieve the forecast 'as it was' at a point in time."
        ),
    ),
    forecast_horizon_minutes: int | None = Query(
        None,
        description=(
            "Forecast horizon filter in minutes. For example, `60` returns only "
            "the 1-hour-ahead forecast value for each target timestep."
        ),
    ),
    model: ValidForecastModel | None = None,
) -> ForecastResponse:
    """Get the solar generation forecast for a specific region.

    Returns a time series of forecast values — power in kW at 30-minute resolution —
    along with model metadata (name, version, creation time, initialisation time).

    By default the window runs from **now** to **48 hours ahead**. Use `start_utc` /
    `end_utc` to override. Historical data is available up to 1 year back.
    Cached for 1 minute.
    """
    is_intraday_only = not _check_country_access(auth, country)
    resolved_id = await _resolve_region_id(region, country, source, db)

    locs = await db.get_locations(
        energy_type=source,
        location_type=None,
        authdata={},  # TODO: add auth when loosed on DP side
        location_uuid=resolved_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{resolved_id}' not found.",
        )
    region = locs[0]
    location_type = region.location_type or models.LocationType.NATION
    rt = country.location_type_to_region_type(location_type)
    _validate_model(model, rt, location_type.name)
    model = _resolve_forecast_model(model, rt, is_intraday_only)

    now = pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    pgvs = await db.get_predicted_generation(
        location_uuid=resolved_id,
        window_start=start_utc or now,
        window_end=end_utc or now + dt.timedelta(days=2),
        energy_type=source,
        location_type=location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        created_cutoff=creation_limit_utc,
        forecast_horizon_minutes=forecast_horizon_minutes or 0,
        forecaster_name=model,
    )

    first = pgvs[0] if pgvs else None
    return ForecastResponse(
        region_name=_location_display_name(region, country),
        capacity_kW=first.capacity_kilowatts if first else 0.0,
        model_name=_internal_to_api_name(first.forecaster_name if first else None, rt),
        model_version=first.forecaster_version if first else None,
        created_utc=first.created_timestamp if first else None,
        init_utc=first.init_timestamp if first else None,
        values=[
            ForecastValue(
                time=v.valid_timestamp,
                power_kW=v.power_kilowatts,
                plevels_kW=v.plevels_kilowatts,
            )
            for v in pgvs
        ],
    )


@router.get(
    "/{country}/{source}/regions/{region}/forecast/last-updated",
    response_model=dt.datetime,
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder, expire=10)
async def get_forecast_last_updated_timestamp(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    region: ValidRegion,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    model: ValidForecastModel | None = None,
) -> dt.datetime:
    """Return the creation time of the most recent forecast for a region.

    Queries the forecast within ±30 minutes of now and returns the `created_utc`
    of the most recent run. Useful for monitoring freshness or driving "last updated"
    indicators in a UI. Cached for 10 seconds.
    """
    is_intraday_only = not _check_country_access(auth, country)
    resolved_id = await _resolve_region_id(region, country, source, db)

    locs = await db.get_locations(
        energy_type=source,
        location_type=None,
        authdata={},  # TODO: add auth when loosed on DP side
        location_uuid=resolved_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{resolved_id}' not found.",
        )
    location_type = locs[0].location_type or models.LocationType.NATION
    rt = country.location_type_to_region_type(location_type)
    model = _resolve_forecast_model(model, rt, is_intraday_only)

    now = dt.datetime.now(tz=dt.UTC)
    pgvs = await db.get_predicted_generation(
        location_uuid=resolved_id,
        window_start=now - dt.timedelta(minutes=30),
        window_end=now + dt.timedelta(minutes=30),
        energy_type=source,
        location_type=location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        forecaster_name=model,
    )
    if not pgvs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No recent forecasts found for this region.",
        )
    return pgvs[0].created_timestamp


@router.get(
    "/{country}/{source}/forecasts/snapshot",
    status_code=status.HTTP_200_OK,
    summary="Get Forecasts at Timestamp",
    response_model=ForecastSnapshot,
)
@cache(key_builder=key_builder, expire=120)
async def get_forecasts_at_time(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidRegionType,
    model_name: ValidForecastModel | None = None,
    model_version: str | None = Query(None, description="Forecast model version."),
    time_utc: dt.datetime | None = Query(
        None,
        description="Forecast target time (UTC). Defaults to now floored to 30 minutes.",
    ),
) -> ForecastSnapshot:
    """Get forecasts for all regions of a given type at a specific time.

    Returns a `ForecastSnapshot` — a single point in time with one forecast value per
    region. Useful for rendering a map of forecast output across an entire country at
    a glance. Cached for 2 minutes.
    """
    is_intraday_only = not _check_country_access(auth, country)
    nation = await _resolve_nation(db, source, country, auth)

    rt = country.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}.",
        )
    _validate_model(model_name, rt, rt.type)
    model_name = _resolve_forecast_model(model_name, rt, is_intraday_only)
    location_type = rt.location_type

    if location_type == models.LocationType.NATION:
        regions = [nation]
    else:
        regions = await db.get_locations(
            energy_type=source,
            location_type=location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{location_type}' in {country.code}.",
        )

    snapshot_time = time_utc or pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=dt.UTC)

    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=[_to_uuid(r.uuid) for r in regions],
        forecaster_name=model_name,
        forecaster_version=model_version,
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        authdata={},
    )

    region_names = {
        _to_uuid(r.uuid): _location_display_name(r, country) for r in regions
    }
    first = snapshot[0] if snapshot else None
    return ForecastSnapshot(
        time=snapshot_time,
        model_name=_internal_to_api_name(first.forecaster_name if first else None, rt),
        model_version=first.forecaster_version if first else None,
        created_utc=first.created_timestamp if first else None,
        init_utc=first.init_timestamp if first else None,
        values=[
            RegionForecastValue(
                region_name=region_names.get(v.location_uuid, ""),
                capacity_kW=v.capacity_kilowatts,
                power_kW=v.power_kilowatts,
                plevels_kW=v.plevels_kilowatts,
            )
            for v in snapshot
        ],
    )


@router.get(
    "/{country}/{source}/forecasts/period",
    status_code=status.HTTP_200_OK,
    summary="Get Forecasts for Period",
)
async def get_forecasts_period(
    source: ValidSource,
    country: CountryParam,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidPeriodRegionType,
    start_utc: dt.datetime | None = Query(
        None,
        description="Start of window (UTC). Defaults to 2 days before now "
        "(floored to the nearest 6 hours).",
    ),
    end_utc: dt.datetime | None = Query(
        None,
        description="End of window (UTC). Defaults to 2 days after now "
        "(floored to the nearest 6 hours).",
    ),
    region_names: list[str] | None = Query(
        None,
        description="Limit to specific region names (e.g. `?region_names=GSP1&region_names=GSP2`).",
    ),
) -> RegionForecastMatrix:
    """Get forecasts for all (or selected) regions across a time window.

    Returns a `RegionForecastMatrix` — a compact columnar structure with a shared
    `times` array and one `power_kW` series per region. Designed for efficiently
    loading all-region forecast data for charts or grid-management tools in a single
    request.

    This endpoint is served entirely from a pre-warmed cache (one key per region).
    It does not make live data-platform calls per request. If the cache has not yet
    been populated after startup, the endpoint returns **503** with a `Retry-After: 60`
    header — retry after a minute. The cache covers a ±2-day window around now,
    refreshed every 24 hours (or on demand via `POST /{country}/{source}/forecasts/refresh`).

    Time-window and region filtering are applied in-memory from the cached data.
    This endpoint fetches only the default forecast model for the selected
    country + region type.

    Model and horizon filters are **not** supported on this endpoint — use
    `GET /{country}/{source}/regions/{region}/forecast` for per-region model selection.
    """
    _check_country_access(auth, country)

    rt = country.get_region_type(region_type)
    _sub_national = [
        r.type
        for r in country.region_types
        if r.location_type != models.LocationType.NATION
    ]
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}. "
            f"Available: {_sub_national}",
        )
    if rt.location_type == models.LocationType.NATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"region_type='{region_type}' is not supported on the period endpoint "
                f"(only sub-national region types are pre-warmed): {_sub_national}. "
                f"Use GET /{country.code}/solar/regions/national/forecast for national-level data."
            ),
        )

    win_start, win_end = _timeseries_window(start_utc, end_utc)

    # This endpoint is cache-only: all per-region timeseries are pre-warmed at startup
    # (and refreshable via POST /forecasts/refresh).  No live DP calls are made per
    # request — the cache is read and window/region filtering is applied in memory.
    # Cache key layout:
    #   {prefix}:v1:timeseries:{COUNTRY}:{source}:{region_type}:{uuid}  — per region values
    #   {prefix}:v1:timeseries:{COUNTRY}:{source}:{region_type}:_meta   — shared model metadata
    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = f"{prefix}:v1:timeseries:{country.code}:{source.name.lower()}:{region_type}"

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forecast cache is being populated, please retry in 60 seconds.",
            headers={"Retry-After": "60"},
        )

    nation = await _resolve_nation(db, source, country, auth)
    regions = await db.get_locations(
        energy_type=source,
        location_type=rt.location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        enclosing_location_uuid=_to_uuid(nation.uuid),
    )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{rt.type}' in {country.code}.",
        )

    if region_names is not None:
        name_set = {n.lower() for n in region_names}
        regions = [r for r in regions if r.name.lower() in name_set]

    raw_list = await asyncio.gather(*[backend.get(f"{base}:{r.uuid}") for r in regions])
    all_region_data: list[tuple] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [ForecastValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time <= win_end]
        all_region_data.append((r, windowed))

    times = [v.time for v in all_region_data[0][1]] if all_region_data else []
    region_series: list[RegionForecast] = []
    for r, windowed in all_region_data:
        plevel_keys = {k for v in windowed for k in v.plevels_kW}
        region_series.append(
            RegionForecast(
                region_name=_location_display_name(r, country),
                capacity_kW=r.capacity_kilowatts,
                power_kW=[v.power_kW for v in windowed],
                plevels_kW={
                    k: [v.plevels_kW.get(k, 0.0) for v in windowed] for k in plevel_keys
                },
            ),
        )

    metadata = json.loads(raw_meta)
    return RegionForecastMatrix(**metadata, times=times, regions=region_series)


@router.post(
    "/{country}/{source}/forecasts/refresh",
    include_in_schema=False,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_forecasts_cache(
    source: ValidSource,
    country: CountryParam,
    background_tasks: BackgroundTasks,
    request: Request,
    auth: AuthDependency,
    region_type: ValidRegionType = "gsp",
) -> Response:
    """Trigger a background re-warm of the forecast period cache.

    Kicks off a background task that re-fetches all per-region forecast data for the
    given country, source, and region type and repopulates the pre-warmed cache.
    Returns 202 immediately; the warm completes in the background.

    Requires the `ocf:admin` permission scope.
    """
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    flag_key = f"{source.name.lower()}:{country.code}:{region_type}"
    if _forecast_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        _warm_v1_forecast_cache,
        request.app,
        source,
        country.code,
        region_type,
    )
    return Response(status_code=202, content="Cache refresh triggered")
