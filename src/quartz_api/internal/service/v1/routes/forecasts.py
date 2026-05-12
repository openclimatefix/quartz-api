"""Forecast routes — per-region, snapshot, and period matrix endpoints."""

# ruff: noqa: ARG001, B008

import asyncio
import datetime as dt
import json
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.service.uk_national.cache import key_builder

from ..cache import _forecast_cache_warming, _warm_v1_forecast_cache
from ..endpoint_types import (
    CountryParam,
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    RegionForecast,
    RegionForecastMatrix,
    RegionForecastValue,
    ValidForecastModel,
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
    "/{country}/{source}/regions/{region_id}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast(
    source: ValidSource,
    country: CountryParam,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: ValidWindowStart = None,
    end_utc: dt.datetime | None = Query(
        None,
        description="End of forecast window (UTC).",
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
        description="Forecast horizon filter in minutes.",
    ),
    model: ValidForecastModel | None = None,
) -> ForecastResponse:
    """Get the solar generation forecast for a specific region.

    Returns a time series of forecast values — power in kW at 30-minute resolution —
    along with model metadata (name, version, creation time, initialisation time).

    By default the window runs from **now** to **48 hours ahead**. Use `start_utc` /
    `end_utc` to override. Historical data is available up to 1 year back.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_id**: region identifier. Accepts a UUID, the string `national`, or a
      region name (case-insensitive exact match). Use `GET /{country}/{source}/regions`
      to browse available regions and their UUIDs.
    - **start_utc**: start of the forecast window (UTC). Defaults to now. Cannot be
      more than 1 year in the past.
    - **end_utc**: end of the forecast window (UTC). Defaults to 48 hours from now.
    - **creation_limit_utc**: if set, only return forecasts that were created at or
      before this time. Useful for retrieving the forecast "as it was known" at a
      specific moment (e.g. `?creation_limit_utc=2026-05-01T09:00:00Z`).
    - **forecast_horizon_minutes**: filter to forecasts made exactly this many minutes
      before the target time. For example, `60` returns only the 1-hour-ahead forecast
      for each target timestep.
    - **model**: forecast model name (e.g. `blend`, `blend_adjust`, `pvnet_intraday`).
      Defaults to the region type's default model. See `/{country}/{source}/region-types`
      for the models available per region type. `blend_adjust` applies a trend-based
      bias correction on top of `blend`.
    """
    is_intraday_only = not _check_country_access(auth, country)
    resolved_id = await _resolve_region_id(region_id, country, source, db)

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
        region_id=_to_uuid(region.uuid),
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
    "/{country}/{source}/regions/{region_id}/forecast/last-updated",
    response_model=dt.datetime,
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder, expire=10)
async def get_forecast_last_updated_timestamp(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    model: ValidForecastModel | None = None,
) -> dt.datetime:
    """Return the creation time of the most recent forecast for a region.

    Queries the forecast within ±30 minutes of now and returns the `created_utc`
    of the most recent run. Useful for monitoring freshness or driving "last updated"
    indicators in a UI. Cached for 10 seconds.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_id**: region identifier — UUID, `national`, or region name.
    - **model**: forecast model name. Defaults to the region type's default model.
    """
    is_intraday_only = not _check_country_access(auth, country)
    resolved_id = await _resolve_region_id(region_id, country, source, db)

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
        description="Forecast target time (UTC).",
    ),
) -> ForecastSnapshot:
    """Get forecasts for all regions of a given type at a specific time.

    Returns a `ForecastSnapshot` — a single point in time with one forecast value per
    region. Useful for rendering a map of forecast output across an entire country at
    a glance. Cached for 2 minutes.

    The default time is now floored to the nearest 30 minutes.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region granularity (e.g. `gsp`, `dno`, `national`). **Required.**
      See `/{country}/{source}/region-types` for valid values.
    - **time_utc**: target datetime (UTC) for the snapshot. Defaults to now floored
      to 30 minutes (e.g. `2026-05-11T14:30:00Z`).
    - **model_name**: forecast model name (e.g. `blend_adjust`). Defaults to the
      region type's default model.
    - **model_version**: optional model version string to pin a specific release.
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

    region_names = {_to_uuid(r.uuid): _location_display_name(r, country) for r in regions}
    first = snapshot[0] if snapshot else None
    return ForecastSnapshot(
        time=snapshot_time,
        model_name=_internal_to_api_name(first.forecaster_name if first else None, rt),
        model_version=first.forecaster_version if first else None,
        created_utc=first.created_timestamp if first else None,
        init_utc=first.init_timestamp if first else None,
        values=[
            RegionForecastValue(
                region_id=v.location_uuid,
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
    region_type: ValidRegionType,
    start_utc: dt.datetime | None = Query(None, description="Start of window (UTC)."),
    end_utc: dt.datetime | None = Query(None, description="End of window (UTC)."),
    region_ids: list[UUID] | None = Query(
        None,
        description="Limit to specific region UUIDs.",
    ),
) -> RegionForecastMatrix:
    """Get forecasts for all (or selected) regions across a time window.

    Returns a `RegionForecastMatrix` — a compact columnar structure with a shared
    `times` array and one `power_kW` series per region. Designed for efficiently
    loading all-region forecast data for charts or grid-management tools in a single
    request.

    This endpoint is served entirely from a pre-warmed cache (one key per region UUID).
    It does not make live data-platform calls per request. If the cache has not yet
    been populated after startup, the endpoint returns **503** with a `Retry-After: 60`
    header — retry after a minute. The cache covers a ±2-day window around now,
    refreshed every 24 hours (or on demand via `POST /{country}/{source}/forecasts/refresh`).

    Time-window and region filtering are applied in-memory from the cached data.
    Model and horizon filters are **not** supported on this endpoint — use
    `GET /{country}/{source}/regions/{region_id}/forecast` for per-region model selection.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region granularity (e.g. `gsp`, `dno`). **Required.**
      See `/{country}/{source}/region-types` for valid values.
    - **start_utc**: start of the window (UTC). Defaults to 2 days before now
      (floored to the nearest 6 hours).
    - **end_utc**: end of the window (UTC). Defaults to 2 days after now
      (floored to the nearest 6 hours).
    - **region_ids**: optional list of region UUIDs to restrict the response to a
      subset of regions (e.g. `?region_ids=uuid1&region_ids=uuid2`).
    """
    _check_country_access(auth, country)

    rt = country.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}.",
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

    if region_ids is not None:
        id_set = set(region_ids)
        regions = [r for r in regions if _to_uuid(r.uuid) in id_set]

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
                region_id=_to_uuid(r.uuid),
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

    #### Parameters
    - **country**: country code (e.g. `GB`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region type to re-warm (e.g. `gsp`). Defaults to `gsp`.
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
