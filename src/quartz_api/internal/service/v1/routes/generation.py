"""Generation routes — per-region observed data, snapshots, and matrix endpoints."""

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

from ..cache import (
    generation_cache_warming,
    generation_period_base_key,
    key_builder,
    warm_v1_generation_cache,
)
from ..endpoint_types import (
    CountryParam,
    GenerationResponse,
    GenerationSnapshot,
    GenerationValue,
    RegionGeneration,
    RegionGenerationMatrix,
    RegionGenerationValue,
    ValidObserver,
    ValidPeriodRegionType,
    ValidRegion,
    ValidRegionType,
    ValidSource,
    ValidWindowStart,
)
from ..helpers import (
    check_country_access,
    location_display_name,
    resolve_nation,
    resolve_region_id,
    timeseries_window,
    to_uuid,
    validate_window,
    window_chunks,
)

router = APIRouter(tags=["Generation"])


@router.get(
    "/{country}/{source}/regions/{region}/generation",
    status_code=status.HTTP_200_OK,
    response_model=GenerationResponse,
)
@cache(key_builder=key_builder, expire=60)
async def get_generation(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    region: ValidRegion,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    observer: ValidObserver = "pvlive_in_day",
    start_utc: ValidWindowStart = None,
    end_utc: dt.datetime | None = Query(
        None,
        description="End of generation window (UTC). Defaults to now.",
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

    NL currently has one observer for solar:

    - **ned_nl** — NED NL estimated solar generation for provinces / national including curtailment.
    """
    check_country_access(auth, country)
    dp_observer = country.resolve_observer(observer)
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
    region = locs[0]
    location_type = region.location_type or models.LocationType.NATION

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    win_start = start_utc or now - dt.timedelta(days=1)
    win_end = end_utc or now
    validate_window(win_start, win_end)
    agvs: list = []
    for chunk_start, chunk_end in window_chunks(win_start, win_end):
        agvs.extend(
            await db.get_actual_generation(
                location_uuid=resolved_id,
                window_start=chunk_start,
                window_end=chunk_end,
                energy_type=source,
                location_type=location_type,
                observer_name=dp_observer,
                authdata={},  # TODO: add auth when loosed on DP side
            ),
        )

    first = agvs[0] if agvs else None
    return GenerationResponse(
        region_name=location_display_name(region, country),
        capacity_kW=first.capacity_kilowatts if first else 0.0,
        observer_name=observer,
        values=[
            GenerationValue(time_utc=v.valid_timestamp, power_kW=v.power_kilowatts)
            for v in agvs
        ],
    )


@router.get(
    "/{country}/{source}/generation/snapshot",
    status_code=status.HTTP_200_OK,
    summary="Get Generation at Timestamp",
    response_model=GenerationSnapshot,
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
        description=(
            "Observation target time (UTC). Defaults to the most recent available "
            "timestamp within the last 6 hours."
        ),
    ),
) -> GenerationSnapshot:
    """Get observed generation for all regions of a given type at a specific time.

    Returns a `GenerationSnapshot` — a single point in time with one observed generation
    value per region. Useful for rendering a map of current solar output across an entire
    country.

    When `time_utc` is omitted the endpoint resolves the most recent available timestamp
    automatically (looking back up to 6 hours) rather than using "now", which would
    rarely have data.
    """
    check_country_access(auth, country)
    dp_observer = country.resolve_observer(observer)
    nation = await resolve_nation(db, source, country, auth)

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
            authdata={},
            enclosing_location_uuid=to_uuid(nation.uuid),
        )

    if len(regions) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No regions found for type '{location_type}' in {country.code}.",
        )

    if time_utc is not None:
        snapshot_time = time_utc if time_utc.tzinfo else time_utc.replace(tzinfo=dt.UTC)
    else:
        # Probe a single region to find the latest available timestamp (up to 6h back).
        # Pick the region with the highest gsp_id (most likely to have recent data) or
        # fall back to the last region in the list.
        probe = max(
            regions,
            key=lambda r: int(r.metadata.get("gsp_id", 0)),
        )
        now = pd.Timestamp.utcnow().replace(tzinfo=dt.UTC).to_pydatetime()
        probe_vals = await db.get_actual_generation(
            location_uuid=probe.uuid,
            window_start=now - dt.timedelta(hours=6),
            window_end=now,
            energy_type=source,
            location_type=location_type,
            observer_name=dp_observer,
            authdata={},
        )
        snapshot_time = (
            probe_vals[-1].valid_timestamp
            if probe_vals
            else pd.Timestamp.utcnow()
            .floor("30min")
            .to_pydatetime()
            .replace(tzinfo=dt.UTC)
        )

    snapshot = await db.get_actual_generation_snapshot(
        location_uuids=[to_uuid(r.uuid) for r in regions],
        snapshot_timestamp_utc=snapshot_time,
        energy_type=source,
        observer_name=dp_observer,
        authdata={},
    )

    region_names = {to_uuid(r.uuid): location_display_name(r, country) for r in regions}
    return GenerationSnapshot(
        time_utc=snapshot_time,
        observer_name=observer,
        values=[
            RegionGenerationValue(
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
    region_type: ValidPeriodRegionType,
    observer: ValidObserver = "pvlive_in_day",
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
) -> RegionGenerationMatrix:
    """Get observed generation for all (or selected) regions across a time window.

    Returns a `RegionGenerationMatrix` — a compact columnar structure with a shared
    `times` array and one `power_kW` series per region. Analogous to the forecast
    period endpoint but for observed (actual) generation data.

    This endpoint is served entirely from a pre-warmed cache (one key per region).
    It does not make live data-platform calls per request. If the cache has not yet
    been populated after startup, the endpoint returns **503** with a `Retry-After: 60`
    header — retry after a minute. The cache covers a ±2-day window around now,
    refreshed every 24 hours (or on demand via `POST /{country}/{source}/generation/refresh`).

    Time-window and region filtering are applied in-memory from the cached data.
    """
    check_country_access(auth, country)

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
                f"Use GET /{country.code}/solar/regions/national/generation "
                f"for national-level data."
            ),
        )
    valid_observers = {
        gs.api_name
        for gs in country.generation_sources
        if gs.source == source.name.lower()
    }
    if observer not in valid_observers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Observer '{observer}' is not available for "
                f"{country.code} {source.name.lower()}. Available: {sorted(valid_observers)}"
            ),
        )
    dp_observer = country.resolve_observer(observer)

    win_start, win_end = timeseries_window(start_utc, end_utc)
    validate_window(win_start, win_end)

    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = generation_period_base_key(
        prefix,
        country.code,
        source.name.lower(),
        region_type,
        dp_observer,
    )

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generation cache is being populated, please retry in 60 seconds.",
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

    raw_list = await asyncio.gather(*[backend.get(f"{base}:{r.uuid}") for r in regions])
    all_region_data: list[tuple] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [GenerationValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time_utc <= win_end]
        all_region_data.append((r, windowed))

    times = [v.time_utc for v in all_region_data[0][1]] if all_region_data else []
    region_series: list[RegionGeneration] = []
    for r, windowed in all_region_data:
        region_series.append(
            RegionGeneration(
                region_name=location_display_name(r, country),
                capacity_kW=r.capacity_kilowatts,
                power_kW=[v.power_kW for v in windowed],
            ),
        )

    metadata = json.loads(raw_meta)
    return RegionGenerationMatrix(**metadata, times_utc=times, regions=region_series)


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
    """
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    dp_observer = country.resolve_observer(observer)
    flag_key = f"{source.name.lower()}:{country.code}:{region_type}:{dp_observer}"
    if generation_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        warm_v1_generation_cache,
        request.app,
        source,
        country.code,
        region_type,
        dp_observer,
    )
    return Response(status_code=202, content="Cache refresh triggered")
