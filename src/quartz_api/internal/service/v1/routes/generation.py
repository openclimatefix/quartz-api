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
    GenerationMatrix,
    GenerationResponse,
    GenerationSnapshot,
    GenerationValue,
    RegionGenerationTimeSeries,
    RegionGenerationValue,
    ValidObserver,
    ValidSource,
)
from ..helpers import (
    CountryCode,
    _country_config,
    _energy_type_for,
    _resolve_nation,
    _resolve_region_id,
    _timeseries_window,
    _to_uuid,
)

router = APIRouter(tags=["Generation"])


@router.get(
    "/{country}/{source}/regions/{region_id}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_region_generation(
    source: ValidSource,
    country: CountryCode,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    observer: ValidObserver = "pvlive_in_day",
    start_utc: dt.datetime | None = Query(None, description="Start of generation window (UTC)."),
    end_utc: dt.datetime | None = Query(None, description="End of generation window (UTC)."),
) -> GenerationResponse:
    """Get observed generation data for a specific region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
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

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    agvs = await db.get_actual_generation(
        location_uuid=resolved_id,
        window_start=start_utc or now - dt.timedelta(days=5),
        window_end=end_utc or now,
        energy_type=energy_type,
        location_type=location_type,
        observer_name=observer,
        authdata={},  # TODO: add auth when loosed on DP side
    )

    first = agvs[0] if agvs else None
    return GenerationResponse(
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
)
@cache(key_builder=key_builder, expire=120)
async def get_generation_snapshot(
    request: Request,
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: str = Query(..., description="Region type (e.g. 'gsp')."),
    observer: ValidObserver = "pvlive_in_day",
    timestamp: dt.datetime | None = Query(
        None, description="Observation target timestamp (UTC).",
    ),
) -> GenerationSnapshot:
    """Get observed generation for all regions of a given type at a specific timestamp."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}.",
        )
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

    snapshot_time = timestamp or pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    if snapshot_time.tzinfo is None:
        snapshot_time = snapshot_time.replace(tzinfo=dt.UTC)

    snapshot = await db.get_actual_generation_snapshot(
        location_uuids=[_to_uuid(r.uuid) for r in regions],
        snapshot_timestamp_utc=snapshot_time,
        energy_type=energy_type,
        observer_name=observer,
        authdata=auth,
    )

    return GenerationSnapshot(
        time=snapshot_time,
        observer_name=observer,
        values=[
            RegionGenerationValue(
                region_id=v.location_uuid,
                capacity_kW=v.capacity_kilowatts,
                power_kW=v.power_kilowatts,
            )
            for v in snapshot
        ],
    )


@router.get(
    "/{country}/{source}/generation/timeseries",
    status_code=status.HTTP_200_OK,
)
async def get_generation_timeseries(
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: str = Query(..., description="Region type (e.g. 'gsp')."),
    observer: ValidObserver = "pvlive_in_day",
    start_utc: dt.datetime | None = Query(None, description="Start of window (UTC)."),
    end_utc: dt.datetime | None = Query(None, description="End of window (UTC)."),
    region_ids: list[UUID] | None = Query(None, description="Limit to specific region UUIDs."),
) -> GenerationMatrix:
    """Get observed generation timeseries for all (or selected) regions across a time window.

    Served from the pre-warmed per-region cache. Returns 503 if the cache has not
    yet been populated — retry after 60 seconds.
    """
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)

    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}.",
        )

    win_start, win_end = _timeseries_window(start_utc, end_utc)

    backend = FastAPICache.get_backend()
    prefix = FastAPICache.get_prefix()
    base = f"{prefix}:v1:timeseries:generation:{country.upper()}:{source}:{region_type}:{observer}"

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Generation cache is being populated, please retry in 60 seconds.",
            headers={"Retry-After": "60"},
        )

    nation = await _resolve_nation(db, energy_type, cfg, auth)
    regions = await db.get_locations(
        energy_type=energy_type,
        location_type=rt.location_type,
        authdata=auth,
        enclosing_location_uuid=_to_uuid(nation.uuid),
    )
    if region_ids is not None:
        id_set = set(region_ids)
        regions = [r for r in regions if _to_uuid(r.uuid) in id_set]

    raw_list = await asyncio.gather(*[backend.get(f"{base}:{r.uuid}") for r in regions])
    region_series: list[RegionGenerationTimeSeries] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [GenerationValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time <= win_end]
        region_series.append(
            RegionGenerationTimeSeries(
                region_id=_to_uuid(r.uuid),
                capacity_kW=r.capacity_kilowatts,
                values=windowed,
            ),
        )

    metadata = json.loads(raw_meta)
    return GenerationMatrix(**metadata, regions=region_series)


@router.post(
    "/{country}/{source}/generation/refresh",
    include_in_schema=False,
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_generation_cache(
    source: ValidSource,
    country: CountryCode,
    background_tasks: BackgroundTasks,
    request: Request,
    auth: AuthDependency,
    region_type: str = Query("gsp", description="Region type to refresh."),
    observer: ValidObserver = "pvlive_in_day",
) -> Response:
    """Trigger a background re-warm of the generation timeseries cache. Requires ocf:admin."""
    if "ocf:admin" not in auth.get("permissions", []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    flag_key = f"{source}:{country}:{region_type}:{observer}"
    if _generation_cache_warming.get(flag_key):
        return Response(status_code=202, content="Cache warm already in progress")
    background_tasks.add_task(
        _warm_v1_generation_cache,
        request.app,
        source,
        country,
        region_type,
        observer,
    )
    return Response(status_code=202, content="Cache refresh triggered")
