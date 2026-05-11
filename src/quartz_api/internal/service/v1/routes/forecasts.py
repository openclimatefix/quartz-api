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
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    RegionForecast,
    RegionForecastMatrix,
    RegionForecastValue,
    ValidForecastModel,
    ValidRegionType,
    ValidSource,
)
from ..helpers import (
    CountryCode,
    _check_country_access,
    _country_config,
    _energy_type_for,
    _resolve_forecast_model,
    _resolve_nation,
    _resolve_region_id,
    _timeseries_window,
    _to_uuid,
    _validate_model,
    _validate_window,
)

router = APIRouter(tags=["Forecasts"])


@router.get(
    "/{country}/{source}/regions/{region_id}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_forecast(
    source: ValidSource,
    country: CountryCode,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: dt.datetime | None = Query(
        None,
        description="Start of forecast window (UTC).",
    ),
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
    """Get the forecast for a specific region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    is_intraday_only = not _check_country_access(auth, cfg)
    resolved_id = await _resolve_region_id(region_id, cfg, energy_type, db)

    locs = await db.get_locations(
        energy_type=energy_type,
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
    rt = cfg.location_type_to_region_type(location_type)
    _validate_model(model, rt, location_type.name)
    _validate_window(start_utc)
    model = _resolve_forecast_model(model, rt, is_intraday_only)

    now = pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    pgvs = await db.get_predicted_generation(
        location_uuid=resolved_id,
        window_start=start_utc or now,
        window_end=end_utc or now + dt.timedelta(days=2),
        energy_type=energy_type,
        location_type=location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        created_cutoff=creation_limit_utc,
        forecast_horizon_minutes=forecast_horizon_minutes or 0,
        forecaster_name=model,
    )

    first = pgvs[0] if pgvs else None
    return ForecastResponse(
        capacity_kW=first.capacity_kilowatts if first else 0.0,
        model_name=first.forecaster_name if first else None,
        model_version=first.forecaster_version if first else None,
        created_time=first.created_timestamp if first else None,
        init_time=first.init_timestamp if first else None,
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
    country: CountryCode,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    model: ValidForecastModel | None = None,
) -> dt.datetime:
    """Return the creation time of the most recent forecast for a region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    is_intraday_only = not _check_country_access(auth, cfg)
    resolved_id = await _resolve_region_id(region_id, cfg, energy_type, db)

    locs = await db.get_locations(
        energy_type=energy_type,
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
    rt = cfg.location_type_to_region_type(location_type)
    model = _resolve_forecast_model(model, rt, is_intraday_only)

    now = dt.datetime.now(tz=dt.UTC)
    pgvs = await db.get_predicted_generation(
        location_uuid=resolved_id,
        window_start=now - dt.timedelta(minutes=30),
        window_end=now + dt.timedelta(minutes=30),
        energy_type=energy_type,
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
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidRegionType,
    model_name: ValidForecastModel | None = None,
    model_version: str | None = Query(None, description="Forecast model version."),
    timestamp: dt.datetime | None = Query(
        None,
        description="Forecast target timestamp (UTC).",
    ),
) -> ForecastSnapshot:
    """Get forecasts for all regions of a given type at a specific timestamp."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    is_intraday_only = not _check_country_access(auth, cfg)
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}.",
        )
    _validate_model(model_name, rt, rt.type)
    model_name = _resolve_forecast_model(model_name, rt, is_intraday_only)
    location_type = rt.location_type

    if location_type == models.LocationType.NATION:
        regions = [nation]
    else:
        regions = await db.get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=auth,
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{location_type}' in {country.upper()}.",
        )

    snapshot_time = timestamp or pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=dt.UTC)

    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=[_to_uuid(r.uuid) for r in regions],
        forecaster_name=model_name,
        forecaster_version=model_version,
        snapshot_timestamp_utc=snapshot_time,
        energy_type=energy_type,
        authdata=auth,
    )

    first = snapshot[0] if snapshot else None
    return ForecastSnapshot(
        time=snapshot_time,
        model_name=first.forecaster_name if first else None,
        model_version=first.forecaster_version if first else None,
        created_time=first.created_timestamp if first else None,
        init_time=first.init_timestamp if first else None,
        values=[
            RegionForecastValue(
                region_id=v.location_uuid,
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
    country: CountryCode,
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

    Served from the pre-warmed per-region cache. Returns 503 if the cache has not
    yet been populated — retry after 60 seconds.
    """
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    _check_country_access(auth, cfg)

    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}.",
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
    base = f"{prefix}:v1:timeseries:{country.upper()}:{source}:{region_type}"

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forecast cache is being populated, please retry in 60 seconds.",
            headers={"Retry-After": "60"},
        )

    nation = await _resolve_nation(db, energy_type, cfg, auth)
    regions = await db.get_locations(
        energy_type=energy_type,
        location_type=rt.location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        enclosing_location_uuid=_to_uuid(nation.uuid),
    )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{rt.type}' in {country.upper()}.",
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
                capacity_kW=r.capacity_kilowatts,
                power_kW=[v.power_kW for v in windowed],
                plevels_kW={k: [v.plevels_kW.get(k, 0.0) for v in windowed] for k in plevel_keys},
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
    country: CountryCode,
    background_tasks: BackgroundTasks,
    request: Request,
    auth: AuthDependency,
    region_type: ValidRegionType = "gsp",
) -> Response:
    """Trigger a background re-warm of the forecast period cache. Requires ocf:admin."""
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    flag_key = f"{source}:{country}:{region_type}"
    if _forecast_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        _warm_v1_forecast_cache,
        request.app,
        source,
        country,
        region_type,
    )
    return Response(status_code=202, content="Cache refresh triggered")
