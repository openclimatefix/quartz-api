"""Generation routes — per-region observed data, snapshots, and matrix endpoints."""

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

from ..cache import _generation_cache_warming, _warm_v1_generation_cache
from ..endpoint_types import (
    CountryParam,
    GenerationResponse,
    GenerationSnapshot,
    GenerationValue,
    RegionGeneration,
    RegionGenerationMatrix,
    RegionGenerationValue,
    ValidObserver,
    ValidRegionType,
    ValidSource,
    ValidWindowStart,
)
from ..helpers import (
    _check_country_access,
    _location_display_name,
    _resolve_nation,
    _resolve_region_id,
    _timeseries_window,
    _to_uuid,
)

router = APIRouter(tags=["Generation"])


@router.get(
    "/{country}/{source}/regions/{region}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_generation(
    source: ValidSource,
    country: CountryParam,
    region: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    observer: ValidObserver = "pvlive_in_day",
    start_utc: ValidWindowStart = None,
    end_utc: dt.datetime | None = Query(
        None, description="End of generation window (UTC).",
    ),
) -> GenerationResponse:
    """Get observed solar generation for a specific region.

    Returns a time series of measured generation values — power in kW — from the
    specified observer. The default window is the **last 24 hours**; use `start_utc` /
    `end_utc` to extend or shift it. Historical data is available up to 1 year back.

    Two observers are available for GB solar:

    - **pvlive_in_day** (default) — PV_Live in-day estimates, updated every 30 minutes.
      These are the most recent values but may be revised later.
    - **pvlive_day_after** — PV_Live day-after final values, available from the following
      morning. Use these when accuracy is more important than latency.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region**: region identifier — UUID, `national`, or region name
      (case-insensitive). Use `GET /{country}/{source}/regions` to browse available regions.
    - **observer**: generation observer name. Defaults to `pvlive_in_day`.
      See `/{country}/{source}/generation-sources` for available observers.
    - **start_utc**: start of the generation window (UTC). Defaults to 24 hours ago.
      Cannot be more than 1 year in the past.
    - **end_utc**: end of the generation window (UTC). Defaults to now.
    """
    _check_country_access(auth, country)
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

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    agvs = await db.get_actual_generation(
        location_uuid=resolved_id,
        window_start=start_utc or now - dt.timedelta(days=1),
        window_end=end_utc or now,
        energy_type=source,
        location_type=location_type,
        observer_name=observer,
        authdata={},  # TODO: add auth when loosed on DP side
    )

    first = agvs[0] if agvs else None
    return GenerationResponse(
        region_id=_to_uuid(region.uuid),
        region_name=_location_display_name(region, country),
        capacity_kW=first.capacity_kilowatts if first else 0.0,
        observer_name=observer,
        values=[
            GenerationValue(time=v.valid_timestamp, power_kW=v.power_kilowatts)
            for v in agvs
        ],
    )


@router.get(
    "/{country}/{source}/generation/snapshot",
    status_code=status.HTTP_200_OK,
    summary="Get Generation at Timestamp",
)
@cache(key_builder=key_builder, expire=120)
async def get_generation_at_timestamp(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidRegionType,
    observer: ValidObserver = "pvlive_in_day",
    time_utc: dt.datetime | None = Query(
        None,
        description="Observation target time (UTC).",
    ),
) -> GenerationSnapshot:
    """Get observed generation for all regions of a given type at a specific time.

    Returns a `GenerationSnapshot` — a single point in time with one observed generation
    value per region. Useful for rendering a map of current solar output across an entire
    country. Cached for 2 minutes.

    The default time is now floored to the nearest 30 minutes.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region granularity (e.g. `gsp`, `dno`, `national`). **Required.**
      See `/{country}/{source}/region-types` for valid values.
    - **time_utc**: target datetime (UTC) for the snapshot. Defaults to now floored
      to 30 minutes (e.g. `2026-05-11T14:30:00Z`).
    - **observer**: generation observer name. Defaults to `pvlive_in_day`.
      See `/{country}/{source}/generation-sources` for available observers.
    """
    _check_country_access(auth, country)
    nation = await _resolve_nation(db, source, country, auth)

    rt = country.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}.",
        )
    location_type = rt.location_type

    if location_type == models.LocationType.NATION:
        regions = [nation]
    else:
        regions = await db.get_locations(
            energy_type=source,
            location_type=location_type,
            authdata=auth,
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

    snapshot = await db.get_actual_generation_snapshot(
        location_uuids=[_to_uuid(r.uuid) for r in regions],
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        observer_name=observer,
        authdata=auth,
    )

    region_names = {_to_uuid(r.uuid): _location_display_name(r, country) for r in regions}
    return GenerationSnapshot(
        time=snapshot_time,
        observer_name=observer,
        values=[
            RegionGenerationValue(
                region_id=v.location_uuid,
                region_name=region_names.get(v.location_uuid, ""),
                capacity_kW=v.capacity_kilowatts,
                power_kW=v.power_kilowatts,
            )
            for v in snapshot
        ],
    )


@router.get(
    "/{country}/{source}/generation/period",
    status_code=status.HTTP_200_OK,
    summary="Get Generation for Period",
)
async def get_generation_period(
    source: ValidSource,
    country: CountryParam,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: ValidRegionType,
    observer: ValidObserver = "pvlive_in_day",
    start_utc: dt.datetime | None = Query(None, description="Start of window (UTC)."),
    end_utc: dt.datetime | None = Query(None, description="End of window (UTC)."),
    region_ids: list[UUID] | None = Query(
        None, description="Limit to specific region UUIDs.",
    ),
    region_names: list[str] | None = Query(
        None,
        description="Limit to specific region names (e.g. `?region_names=GSP1&region_names=GSP2`).",
    ),
) -> RegionGenerationMatrix:
    """Get observed generation for all (or selected) regions across a time window.

    Returns a `RegionGenerationMatrix` — a compact columnar structure with a shared
    `times` array and one `power_kW` series per region. Analogous to the forecast
    period endpoint but for observed (actual) generation data.

    This endpoint is served entirely from a pre-warmed cache (one key per region UUID).
    It does not make live data-platform calls per request. If the cache has not yet
    been populated after startup, the endpoint returns **503** with a `Retry-After: 60`
    header — retry after a minute. The cache covers a ±2-day window around now,
    refreshed every 24 hours (or on demand via `POST /{country}/{source}/generation/refresh`).

    Time-window and region filtering are applied in-memory from the cached data.

    #### Parameters
    - **country**: country code (e.g. `GB`, `NL`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region granularity (e.g. `gsp`, `dno`). **Required.**
      See `/{country}/{source}/region-types` for valid values.
    - **observer**: generation observer name. Defaults to `pvlive_in_day`.
      See `/{country}/{source}/generation-sources` for available observers.
    - **start_utc**: start of the window (UTC). Defaults to 2 days before now
      (floored to the nearest 6 hours).
    - **end_utc**: end of the window (UTC). Defaults to 2 days after now
      (floored to the nearest 6 hours).
    - **region_ids**: optional list of region UUIDs to restrict the response to a
      subset of regions (e.g. `?region_ids=uuid1&region_ids=uuid2`).
    - **region_names**: optional list of region names to restrict the response to a
      subset of regions (e.g. `?region_names=GSP1&region_names=GSP2`).
      Can be combined with `region_ids`; the union of both sets is returned.
    """
    _check_country_access(auth, country)

    rt = country.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.code}.",
        )

    win_start, win_end = _timeseries_window(start_utc, end_utc)

    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = (
        f"{prefix}:v1:timeseries:generation"
        f":{country.code}:{source.name.lower()}:{region_type}:{observer}"
    )

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generation cache is being populated, please retry in 60 seconds.",
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

    if region_ids is not None or region_names is not None:
        id_set = set(region_ids or [])
        name_set = {n.lower() for n in (region_names or [])}
        regions = [
            r for r in regions
            if _to_uuid(r.uuid) in id_set or r.name.lower() in name_set
        ]

    raw_list = await asyncio.gather(*[backend.get(f"{base}:{r.uuid}") for r in regions])
    all_region_data: list[tuple] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [GenerationValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time <= win_end]
        all_region_data.append((r, windowed))

    times = [v.time for v in all_region_data[0][1]] if all_region_data else []
    region_series: list[RegionGeneration] = []
    for r, windowed in all_region_data:
        region_series.append(
            RegionGeneration(
                region_id=_to_uuid(r.uuid),
                region_name=_location_display_name(r, country),
                capacity_kW=r.capacity_kilowatts,
                power_kW=[v.power_kW for v in windowed],
            ),
        )

    metadata = json.loads(raw_meta)
    return RegionGenerationMatrix(**metadata, times=times, regions=region_series)


@router.post(
    "/{country}/{source}/generation/refresh",
    include_in_schema=False,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_generation_cache(
    source: ValidSource,
    country: CountryParam,
    background_tasks: BackgroundTasks,
    request: Request,
    auth: AuthDependency,
    region_type: ValidRegionType = "gsp",
    observer: ValidObserver = "pvlive_in_day",
) -> Response:
    """Trigger a background re-warm of the generation period cache.

    Kicks off a background task that re-fetches all per-region observed generation
    data for the given country, source, region type, and observer, then repopulates
    the pre-warmed cache. Returns 202 immediately; the warm completes in the background.

    Requires the `ocf:admin` permission scope.

    #### Parameters
    - **country**: country code (e.g. `GB`).
    - **source**: energy source — currently only `solar` is supported.
    - **region_type**: region type to re-warm (e.g. `gsp`). Defaults to `gsp`.
    - **observer**: generation observer to re-warm. Defaults to `pvlive_in_day`.
    """
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    flag_key = f"{source.name.lower()}:{country.code}:{region_type}:{observer}"
    if _generation_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        _warm_v1_generation_cache,
        request.app,
        source,
        country.code,
        region_type,
        observer,
    )
    return Response(status_code=202, content="Cache refresh triggered")
