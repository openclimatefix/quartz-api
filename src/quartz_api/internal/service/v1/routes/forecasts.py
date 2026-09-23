"""Forecast routes — per-region, snapshot, and period matrix endpoints."""

# ruff: noqa: ARG001, B008

import asyncio
import datetime as dt
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.constants import SUPPORT_EMAIL
from quartz_api.internal.middleware.auth import AuthDependency

from ..auth_scopes import ADMIN_PERMISSION
from ..cache import (
    forecast_cache_warming,
    forecast_period_base_key,
    key_builder,
    warm_v1_forecast_cache,
)
from ..country_config import RegionTypeConfig
from ..endpoint_types import (
    PERIOD_RESPONSES,
    REGION_RESPONSES,
    SNAPSHOT_RESPONSES,
    CountryParam,
    DeprecatedForecastModel,
    DetailLevel,
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    RegionForecast,
    RegionForecastMatrix,
    RegionForecastValue,
    ValidDetail,
    ValidForecastModel,
    ValidForecastModelVersion,
    ValidPeriodRegionType,
    ValidRegion,
    ValidRegionType,
    ValidSource,
    ValidWindowEnd,
    ValidWindowStart,
)
from ..helpers import (
    api_facing_model_errors,
    check_country_access,
    fetch_api_region,
    internal_to_api_name,
    latest_capacity,
    latest_run_timestamps,
    location_display_name,
    parse_forecast_metadata,
    plevel_sort_key,
    region_metadata,
    resolve_forecast_model,
    resolve_model_param,
    resolve_nation,
    resolve_region_id,
    sort_plevels,
    timeseries_window,
    to_uuid,
    validate_model,
    validate_window,
    window_chunks,
)

router = APIRouter(tags=["Forecasts"])


@router.get(
    "/{country}/{source}/regions/{region}/forecast",
    responses=REGION_RESPONSES,
    status_code=status.HTTP_200_OK,
    response_model=ForecastResponse,
    response_model_exclude_none=True,
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
    end_utc: ValidWindowEnd = None,
    creation_limit_utc: dt.datetime | None = Query(
        None,
        description=(
            "Only include forecasts created at or before this time (UTC). "
            "Use to retrieve the forecast 'as it was' at a point in time."
        ),
    ),
    horizon_minutes: int | None = Query(
        None,
        ge=0,
        description=(
            "Forecast horizon filter in minutes. For example, `60` returns only "
            "the 1-hour-ahead forecast value for each target timestep."
        ),
    ),
    detail: ValidDetail = DetailLevel.values,
    model_name: ValidForecastModel | None = None,
    model_version: ValidForecastModelVersion = None,
    model: DeprecatedForecastModel = None,
    adjusted: bool = Query(
        True,
        description=(
            "Apply the trend adjuster, which corrects the forecast using the last "
            "week of observed error. On by default. Ignored for region types with no "
            "adjusted model variants (e.g. GB `gsp`)."
        ),
    ),
) -> ForecastResponse:
    """Get the solar generation forecast for a specific region.

    Returns a time series of forecast values (power in kW) along with model metadata
    (name, version, creation time, initialisation time).

    By default the window runs from **now** to **up to 48 hours ahead**, though a
    response reaches only as far as the latest model run does. Use `start_utc` /
    `end_utc` to override. Historical data is available up to 1 year back.
    """
    model_name = resolve_model_param(model_name, model)
    is_intraday_only = not check_country_access(auth, country)
    resolved_id = await resolve_region_id(region, country, source, db)

    region = await fetch_api_region(resolved_id, country, source, db)
    location_type = region.location_type or models.LocationType.NATION
    rt = country.location_type_to_region_type(location_type)
    # The region type slug the caller knows, not the internal LocationType enum
    # name, which reads 'GSP' where the API says 'gsp'.
    validate_model(model_name, rt, rt.type if rt else location_type.name.lower())
    model_name = resolve_forecast_model(model_name, rt, is_intraday_only, adjusted)

    now = country.floor_to_time_step(dt.datetime.now(tz=dt.UTC))
    win_start = start_utc or now
    win_end = end_utc or now + dt.timedelta(days=2)
    validate_window(win_start, win_end)
    pgvs: list = []
    with api_facing_model_errors(model_name, rt):
        for chunk_start, chunk_end in window_chunks(win_start, win_end):
            pgvs.extend(
                await db.get_predicted_generation(
                    location_uuid=resolved_id,
                    window_start=chunk_start,
                    window_end=chunk_end,
                    energy_type=source,
                    location_type=location_type,
                    authdata={},  # TODO: add auth when loosed on DP side
                    created_cutoff=creation_limit_utc,
                    forecast_horizon_minutes=horizon_minutes or 0,
                    forecaster_name=model_name,
                    forecaster_version=model_version,
                ),
            )

    first = pgvs[0] if pgvs else None
    last_updated, latest_init = latest_run_timestamps(pgvs)
    return ForecastResponse(
        region_name=location_display_name(region, country),
        capacity_kW=latest_capacity(pgvs),
        model_name=internal_to_api_name(first.forecaster_name if first else None, rt),
        model_version=first.forecaster_version if first else None,
        last_updated_utc=last_updated,
        latest_init_utc=latest_init,
        horizon_minutes=horizon_minutes,
        metadata=region_metadata(region, detail),
        values=[_forecast_value(v, rt, detail) for v in pgvs],
    )


def _forecast_value(
    pgv: models.PredictedGenerationValue,
    rt: RegionTypeConfig,
    detail: DetailLevel,
) -> ForecastValue:
    """Build a ForecastValue, promoting per-value run metadata when asked for."""
    value = ForecastValue(
        time_utc=pgv.valid_timestamp,
        power_kW=pgv.power_kilowatts,
        plevels_kW=sort_plevels(pgv.plevels_kilowatts),
    )
    if detail == DetailLevel.values:
        return value
    value.last_updated_utc = pgv.created_timestamp
    value.latest_init_utc = pgv.init_timestamp
    value.model_name = internal_to_api_name(pgv.forecaster_name, rt)
    value.model_version = pgv.forecaster_version
    value.capacity_kW = pgv.capacity_kilowatts
    if detail == DetailLevel.full:
        value.metadata = parse_forecast_metadata(pgv.metadata)
    return value


@router.get(
    "/{country}/{source}/regions/{region}/forecast/last-updated",
    responses=REGION_RESPONSES,
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
    model_name: ValidForecastModel | None = None,
    model_version: ValidForecastModelVersion = None,
    model: DeprecatedForecastModel = None,
    adjusted: bool = Query(
        True,
        description=(
            "Apply the trend adjuster, which corrects the forecast using the last "
            "week of observed error. On by default. Ignored for region types with no "
            "adjusted model variants (e.g. GB `gsp`)."
        ),
    ),
) -> dt.datetime:
    """Return the creation time of the most recent forecast for a region.

    Queries the forecast within ±30 minutes of now and returns the `last_updated_utc`
    of the most recent run. Useful for monitoring freshness or driving "last updated"
    indicators in a UI.
    """
    model_name = resolve_model_param(model_name, model)
    is_intraday_only = not check_country_access(auth, country)
    resolved_id = await resolve_region_id(region, country, source, db)

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
    # The region type slug the caller knows, not the internal LocationType enum
    # name, which reads 'GSP' where the API says 'gsp'.
    validate_model(model_name, rt, rt.type if rt else location_type.name.lower())
    model_name = resolve_forecast_model(model_name, rt, is_intraday_only, adjusted)

    now = dt.datetime.now(tz=dt.UTC)
    with api_facing_model_errors(model_name, rt):
        pgvs = await db.get_predicted_generation(
            location_uuid=resolved_id,
            window_start=now - dt.timedelta(minutes=30),
            window_end=now + dt.timedelta(minutes=30),
            energy_type=source,
            location_type=location_type,
            authdata={},  # TODO: add auth when loosed on DP side
            forecaster_name=model_name,
            forecaster_version=model_version,
        )
    if not pgvs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No recent forecasts found for this region.",
        )
    return pgvs[0].created_timestamp


@router.get(
    "/{country}/{source}/forecasts/snapshot",
    responses=SNAPSHOT_RESPONSES,
    status_code=status.HTTP_200_OK,
    summary="Get Forecasts at Timestamp",
    response_model=ForecastSnapshot,
    response_model_exclude_none=True,
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
    model_version: ValidForecastModelVersion = None,
    model: DeprecatedForecastModel = None,
    adjusted: bool = Query(
        True,
        description=(
            "Apply the trend adjuster, which corrects the forecast using the last "
            "week of observed error. On by default. Ignored for region types with no "
            "adjusted model variants (e.g. GB `gsp`)."
        ),
    ),
    time_utc: dt.datetime | None = Query(
        None,
        description=(
            "Forecast target time (UTC). Rounded down to the country's time step "
            "(e.g. 30 minutes for GB, 15 for NL); the `time_utc` in the response is "
            "the timestamp actually used. Defaults to now."
        ),
    ),
) -> ForecastSnapshot:
    """Get forecasts for all regions of a given type at a specific time.

    Returns a `ForecastSnapshot`: a single point in time with one forecast value per
    region. Useful for rendering a map of forecast output across an entire country at
    a glance.
    """
    model_name = resolve_model_param(model_name, model)
    is_intraday_only = not check_country_access(auth, country)
    nation = await resolve_nation(db, source, country, auth)

    rt = country.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}.",
        )
    validate_model(model_name, rt, rt.type)
    model_name = resolve_forecast_model(
        model_name,
        rt,
        is_intraday_only,
        adjusted,
    )
    location_type = rt.location_type

    if location_type == models.LocationType.NATION:
        regions = [nation]
    else:
        regions = await db.get_locations(
            energy_type=source,
            location_type=location_type,
            authdata={},
            enclosing_location_uuid=to_uuid(nation.uuid),
        )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{location_type}' in {country.code}.",
        )

    # Floored whether supplied or defaulted: values exist only on the country's time
    # step, so an unfloored timestamp matched nothing and came back as an empty snapshot.
    snapshot_time = country.floor_to_time_step(
        time_utc if time_utc is not None else dt.datetime.now(tz=dt.UTC),
    )

    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=[to_uuid(r.uuid) for r in regions],
        forecaster_name=model_name,
        forecaster_version=model_version,
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        authdata={},
    )

    region_names = {to_uuid(r.uuid): location_display_name(r, country) for r in regions}
    first = snapshot[0] if snapshot else None
    last_updated, latest_init = latest_run_timestamps(snapshot)
    return ForecastSnapshot(
        time_utc=snapshot_time,
        model_name=internal_to_api_name(first.forecaster_name if first else None, rt),
        model_version=first.forecaster_version if first else None,
        last_updated_utc=last_updated,
        latest_init_utc=latest_init,
        values=[
            RegionForecastValue(
                region_name=region_names.get(v.location_uuid, ""),
                capacity_kW=v.capacity_kilowatts,
                power_kW=v.power_kilowatts,
                plevels_kW=sort_plevels(v.plevels_kilowatts) or None,
            )
            for v in snapshot
        ],
    )


@router.get(
    "/{country}/{source}/forecasts/period",
    responses=PERIOD_RESPONSES,
    status_code=status.HTTP_200_OK,
    summary="Get Forecasts for Current Period",
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
    end_utc: ValidWindowEnd = None,
    region_names: list[str] | None = Query(
        None,
        description="Limit to specific region names (e.g. `?region_names=GSP1&region_names=GSP2`).",
    ),
) -> RegionForecastMatrix:
    """Get forecasts for all (or selected) regions across a time window.

    Returns a `RegionForecastMatrix`: a compact columnar structure with a shared
    `times` array and one `power_kW` series per region. Designed for efficiently
    loading all-region forecast data for charts or grid-management tools in a single
    request.

    This endpoint is served entirely from a pre-warmed cache (one key per region).
    It does not make live data-platform calls per request. If the cache has not yet
    been populated after startup, the endpoint returns **503** with a `Retry-After: 60`
    header, so retry after a minute. The cache covers a ±2-day window around now,
    refreshed every 24 hours (or on demand via `POST /{country}/{source}/forecasts/refresh`).

    Time-window and region filtering are applied in-memory from the cached data.
    This endpoint fetches only the default forecast model for the selected
    country + region type.

    Model and horizon filters are **not** supported on this endpoint; use
    `GET /{country}/{source}/regions/{region}/forecast` for per-region model selection.

    Not available to intraday-only subscriptions: this endpoint serves the pre-warmed
    blend, and there is no intraday equivalent to fall back to.
    """
    # Every other forecast route downgrades an intraday-only caller to the intraday
    # models. This one serves one pre-warmed model per region type, so there is nothing
    # to downgrade to — the only honest answers are the blend or a 403.
    if not check_country_access(auth, country):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This endpoint serves the blend model only, which is not included in "
                "intraday-only subscriptions. Use "
                f"GET /{country.code}/{source.name.lower()}/regions/{{region}}/forecast "
                f"to request an intraday model per region, or contact {SUPPORT_EMAIL} "
                "to upgrade."
            ),
        )

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

    win_start, win_end = timeseries_window(start_utc, end_utc)
    validate_window(win_start, win_end)

    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = forecast_period_base_key(
        prefix,
        country.code,
        source.name.lower(),
        region_type,
    )

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forecast cache is being populated, please retry in 60 seconds.",
            headers={"Retry-After": "60"},
        )

    nation = await resolve_nation(db, source, country, auth)
    regions = await db.get_locations(
        energy_type=source,
        location_type=rt.location_type,
        authdata={},  # TODO: add auth when loosed on DP side
        enclosing_location_uuid=to_uuid(nation.uuid),
    )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{rt.type}' in {country.code}.",
        )

    if region_names is not None:
        name_set = {n.lower() for n in region_names}
        regions = [
            r
            for r in regions
            if r.name.lower() in name_set
            or location_display_name(r, country).lower() in name_set
        ]
        # A name that matches nothing used to be dropped silently, so a typo came back
        # as a 200 with fewer regions than were asked for, or none at all.
        found = {r.name.lower() for r in regions} | {
            location_display_name(r, country).lower() for r in regions
        }
        unknown = sorted(n for n in region_names if n.lower() not in found)
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"No {region_type} region in {country.code} named: "
                    f"{unknown}. Use GET /{country.code}/{source.name.lower()}/regions"
                    f"?region_type={region_type} to list them."
                ),
            )

    raw_list = await asyncio.gather(*[backend.get(f"{base}:{r.uuid}") for r in regions])
    all_region_data: list[tuple] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [ForecastValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time_utc <= win_end]
        all_region_data.append((r, windowed))

    times = [v.time_utc for v in all_region_data[0][1]] if all_region_data else []
    region_series: list[RegionForecast] = []
    for r, windowed in all_region_data:
        plevel_keys = {k for v in windowed for k in v.plevels_kW}
        region_series.append(
            RegionForecast(
                region_name=location_display_name(r, country),
                capacity_kW=r.capacity_kilowatts,
                power_kW=[v.power_kW for v in windowed],
                plevels_kW={
                    k: [v.plevels_kW.get(k, 0.0) for v in windowed]
                    for k in sorted(plevel_keys, key=plevel_sort_key)
                },
            ),
        )

    metadata = json.loads(raw_meta)
    return RegionForecastMatrix(**metadata, times_utc=times, regions=region_series)


@router.post(
    "/{country}/{source}/forecasts/refresh",
    responses=SNAPSHOT_RESPONSES,
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
    if ADMIN_PERMISSION not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    flag_key = f"{source.name.lower()}:{country.code}:{region_type}"
    if forecast_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        warm_v1_forecast_cache,
        request.app,
        source,
        country.code,
        region_type,
    )
    return Response(status_code=202, content="Cache refresh triggered")
