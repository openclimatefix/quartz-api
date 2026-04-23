"""The v1 API router providing a unified view of regions, forecasts, and generation."""

# ruff: noqa: ARG001, B008

import asyncio
import datetime as dt
import json
import logging
from enum import StrEnum
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response
from fastapi_cache import FastAPICache
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from quartz_api.internal.service.uk_national.cache import key_builder

from .country_config import (
    COUNTRIES,
    VALID_COUNTRY_CODES,
    CountryConfig,
    RegionTypeConfig,
)
from .endpoint_types import (
    CountryDetail,
    ForecastMatrix,
    ForecastModel,
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    GenerationMatrix,
    GenerationResponse,
    GenerationSnapshot,
    GenerationSource,
    GenerationValue,
    RegionDetail,
    RegionForecastTimeSeries,
    RegionForecastValue,
    RegionGenerationTimeSeries,
    RegionGenerationValue,
    RegionSummary,
    RegionType,
    Source,
    ValidForecastModel,
    ValidObserver,
    ValidSource,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["v1"])

# Derived at import time from country_config so Swagger renders a dropdown of valid values.
CountryCode = StrEnum("CountryCode", {k: k for k in COUNTRIES})

# Per-combination warming flags: key is "{source}:{country}:{region_type}" or
# "{source}:{country}:{region_type}:{observer}" for generation.
_forecast_cache_warming: dict[str, bool] = {}
_generation_cache_warming: dict[str, bool] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _energy_type_for(source: str) -> models.EnergyType:
    """Map a source path parameter to an EnergyType."""
    if source == "solar":
        return models.EnergyType.SOLAR
    if source == "wind":
        return models.EnergyType.WIND
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid source '{source}'. Must be 'wind' or 'solar'.",
    )


def _country_config(country: str) -> CountryConfig:
    """Look up country config, raising 404 if unknown."""
    upper = country.upper()
    if upper not in VALID_COUNTRY_CODES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown country '{country}'. "
            f"Supported: {sorted(VALID_COUNTRY_CODES)}",
        )
    return COUNTRIES[upper]


async def _resolve_nation(
    db: models.StorageInterface,
    energy_type: models.EnergyType,
    country_cfg: CountryConfig,
    auth: AuthDependency,
) -> models.Location:
    """Resolve a country config to its nation Location in the data platform."""
    nations = await db.get_locations(
        energy_type=energy_type,
        location_type=models.LocationType.NATION,
        authdata=auth,
    )
    matches = [n for n in nations if n.name.lower() == country_cfg.nation_name.lower()]
    if len(matches) == 0:
        available = [n.name for n in nations]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No nation found with name '{country_cfg.nation_name}'. Available: {available}",
        )
    return matches[0]


def _location_to_summary(
    loc: models.Location,
    country_cfg: CountryConfig,
) -> RegionSummary:
    """Convert an internal Location to a RegionSummary."""
    region_type_name: str | None = None
    if loc.location_type is not None:
        rt = country_cfg.location_type_to_region_type(loc.location_type)
        if rt is not None:
            region_type_name = rt.type
    return RegionSummary(
        id=loc.uuid,
        name=loc.name,
        type=region_type_name,
        capacity_kW=loc.capacity_kilowatts,
        latitude=loc.latitude,
        longitude=loc.longitude,
    )


def _location_to_detail(
    loc: models.Location,
    country_cfg: CountryConfig,
) -> RegionDetail:
    """Convert an internal Location to a RegionDetail."""
    region_type_name: str | None = None
    if loc.location_type is not None:
        rt = country_cfg.location_type_to_region_type(loc.location_type)
        if rt is not None:
            region_type_name = rt.type
    return RegionDetail(
        id=loc.uuid,
        name=loc.name,
        type=region_type_name,
        capacity_kW=loc.capacity_kilowatts,
        latitude=loc.latitude,
        longitude=loc.longitude,
        metadata=loc.metadata,
    )


def _to_uuid(val: str | UUID) -> UUID:
    """Convert a string or UUID to UUID."""
    return UUID(val) if isinstance(val, str) else val


def _validate_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    region_type_label: str,
) -> None:
    """Raise 400 if model is provided but not listed for the region type."""
    if model is None or rt is None or not rt.forecast_models:
        return
    valid = {f.name for f in rt.forecast_models}
    if model not in valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Model '{model}' is not available for region type '{region_type_label}'. "
            f"Available: {sorted(valid)}",
        )


async def _resolve_region_id(
    region_id: str,
    cfg: CountryConfig,
    energy_type: models.EnergyType,
    db: models.StorageInterface,
) -> UUID:
    """Resolve a region_id path param: 'national' slug → nation UUID, else parse as UUID."""
    if region_id != "national":
        try:
            return UUID(region_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="region_id must be 'national' or a valid UUID.",
            ) from None
    nations = await db.get_locations(
        energy_type=energy_type,
        location_type=models.LocationType.NATION,
        authdata={},
    )
    for n in nations:
        if n.name.lower() == cfg.nation_name.lower():
            return n.uuid
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"National region for '{cfg.nation_name}' not found.",
    )


def _timeseries_window(
    start_utc: dt.datetime | None,
    end_utc: dt.datetime | None,
) -> tuple[dt.datetime, dt.datetime]:
    """Return canonical start/end window, applying the 6-hour-floored ±2-day default."""
    now = pd.Timestamp.utcnow().floor("6h").to_pydatetime().replace(tzinfo=dt.UTC)
    win_start = start_utc if start_utc is not None else now - dt.timedelta(days=2)
    win_end = end_utc if end_utc is not None else now + dt.timedelta(days=2)
    if win_start.tzinfo is None:
        win_start = win_start.replace(tzinfo=dt.UTC)
    if win_end.tzinfo is None:
        win_end = win_end.replace(tzinfo=dt.UTC)
    return win_start, win_end


# ---------------------------------------------------------------------------
# Cache warming
# ---------------------------------------------------------------------------


async def _warm_v1_forecast_cache(
    app: object,
    source: str,
    country: str,
    region_type: str,
) -> None:
    """Pre-warm per-region forecast timeseries cache for one (source, country, region_type)."""
    flag_key = f"{source}:{country}:{region_type}"
    _forecast_cache_warming[flag_key] = True
    try:
        db = app.dependency_overrides.get(models.get_storage_client, lambda: None)()
        if db is None:
            log.warning("v1 forecast cache warm skipped: storage client not configured")
            return

        cfg = COUNTRIES.get(country.upper())
        if cfg is None:
            return
        rt = cfg.get_region_type(region_type)
        if rt is None:
            return

        energy_type = _energy_type_for(source)
        win_start, win_end = _timeseries_window(None, None)

        nations = await db.get_locations(
            energy_type=energy_type,
            location_type=models.LocationType.NATION,
            authdata={},
        )
        nation = next(
            (n for n in nations if n.name.lower() == cfg.nation_name.lower()),
            None,
        )
        if nation is None:
            log.warning(
                "v1 forecast cache warm: nation '%s' not found",
                cfg.nation_name,
            )
            return

        regions = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = f"{prefix}:v1:timeseries:{country.upper()}:{source}:{region_type}"
        first_pgv = None

        for i, region in enumerate(regions):
            pgvs = await db.get_predicted_generation(
                location_uuid=region.uuid,
                window_start=win_start,
                window_end=win_end,
                energy_type=energy_type,
                location_type=rt.location_type,
                authdata={},
            )
            if pgvs and first_pgv is None:
                first_pgv = pgvs[0]
            values = [
                {
                    "time": v.valid_timestamp.isoformat(),
                    "power_kW": v.power_kilowatts,
                    "plevels_kW": v.plevels_kilowatts,
                }
                for v in pgvs
            ]
            await backend.set(f"{base}:{region.uuid}", json.dumps(values), expire=86400)
            if (i + 1) % 20 == 0:
                log.info(
                    "v1 forecast cache warm %s/%s/%s: %d/%d regions",
                    country,
                    source,
                    region_type,
                    i + 1,
                    len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        if first_pgv:
            created = first_pgv.created_timestamp
            init = first_pgv.init_timestamp
            meta = {
                "model_name": first_pgv.forecaster_name,
                "model_version": first_pgv.forecaster_version,
                "created_time": created.isoformat() if created else None,
                "init_time": init.isoformat() if init else None,
            }
            await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 forecast cache warmed: %s/%s/%s — %d regions",
            country,
            source,
            region_type,
            len(regions),
        )
    except Exception:
        log.exception(
            "v1 forecast cache warm failed: %s/%s/%s",
            source,
            country,
            region_type,
        )
    finally:
        _forecast_cache_warming[flag_key] = False


async def _warm_v1_generation_cache(
    app: object,
    source: str,
    country: str,
    region_type: str,
    observer: str,
) -> None:
    """Pre-warm per-region generation timeseries cache for one combination."""
    flag_key = f"{source}:{country}:{region_type}:{observer}"
    _generation_cache_warming[flag_key] = True
    try:
        db = app.dependency_overrides.get(models.get_storage_client, lambda: None)()
        if db is None:
            log.warning(
                "v1 generation cache warm skipped: storage client not configured",
            )
            return

        cfg = COUNTRIES.get(country.upper())
        if cfg is None:
            return
        rt = cfg.get_region_type(region_type)
        if rt is None:
            return

        energy_type = _energy_type_for(source)
        win_start, win_end = _timeseries_window(None, None)

        nations = await db.get_locations(
            energy_type=energy_type,
            location_type=models.LocationType.NATION,
            authdata={},
        )
        nation = next(
            (n for n in nations if n.name.lower() == cfg.nation_name.lower()),
            None,
        )
        if nation is None:
            log.warning(
                "v1 generation cache warm: nation '%s' not found",
                cfg.nation_name,
            )
            return

        regions = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = (
            f"{prefix}:v1:timeseries:generation"
            f":{country.upper()}:{source}:{region_type}:{observer}"
        )

        for i, region in enumerate(regions):
            agvs = await db.get_actual_generation(
                location_uuid=region.uuid,
                window_start=win_start,
                window_end=win_end,
                energy_type=energy_type,
                location_type=rt.location_type,
                authdata={},
                observer_name=observer,
            )
            values = [
                {
                    "time": v.valid_timestamp.isoformat(),
                    "power_kW": v.power_kilowatts,
                }
                for v in agvs
            ]
            await backend.set(f"{base}:{region.uuid}", json.dumps(values), expire=86400)
            if (i + 1) % 20 == 0:
                log.info(
                    "v1 generation cache warm %s/%s/%s/%s: %d/%d regions",
                    source,
                    country,
                    region_type,
                    observer,
                    i + 1,
                    len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        meta = {"observer_name": observer}
        await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 generation cache warmed: %s/%s/%s/%s — %d regions",
            source,
            country,
            region_type,
            observer,
            len(regions),
        )
    except Exception:
        log.exception(
            "v1 generation cache warm failed: %s/%s/%s/%s",
            source,
            country,
            region_type,
            observer,
        )
    finally:
        _generation_cache_warming[flag_key] = False


async def _warm_all_v1_caches(app: object) -> None:
    """Warm all v1 timeseries caches derived from the COUNTRIES config.

    Targets are inferred automatically: adding generation sources or a new country
    to country_config.py is sufficient to include them in the pre-warm.
    """
    await asyncio.sleep(5)
    tasks = []
    for country_code, cfg in COUNTRIES.items():
        source = "solar"
        for rt in cfg.region_types:
            if rt.location_type == models.LocationType.NATION:
                continue
            if rt.forecast_models:
                tasks.append(
                    _warm_v1_forecast_cache(app, source, country_code, rt.type),
                )
            for gen_src in cfg.generation_sources:
                tasks.append(
                    _warm_v1_generation_cache(
                        app,
                        source,
                        country_code,
                        rt.type,
                        gen_src.name,
                    ),
                )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("v1 cache warm subtask failed", exc_info=r)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/sources",
    status_code=status.HTTP_200_OK,
)
async def get_sources(
    auth: AuthDependency,
) -> list[Source]:
    """List available forecast energy sources."""
    return [
        Source(name="solar", label="Solar"),
        Source(name="wind", label="Wind"),
    ]


@router.get(
    "/countries",
    status_code=status.HTTP_200_OK,
)
async def get_countries(
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> list[CountryDetail]:
    """List available countries with full capability manifest (region types, models, sources)."""
    nations = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata=auth,
    )
    result = []
    for country_code, cfg in COUNTRIES.items():
        nation = next(
            (n for n in nations if n.name.lower() == cfg.nation_name.lower()),
            None,
        )
        if nation is None:
            continue
        result.append(
            CountryDetail(
                country=country_code,
                region_id=nation.uuid,
                name=nation.name,
                capacity_kW=nation.capacity_kilowatts,
                latitude=nation.latitude,
                longitude=nation.longitude,
                region_types=[
                    RegionType(
                        type=rt.type,
                        label=rt.label,
                        level=rt.level,
                        forecast_models=[
                            ForecastModel(name=f.name, label=f.label)
                            for f in rt.forecast_models
                        ],
                    )
                    for rt in cfg.region_types
                ],
                generation_sources=[
                    GenerationSource(source=gs.source, name=gs.name, label=gs.label)
                    for gs in cfg.generation_sources
                ],
            ),
        )
    return result


@router.get(
    "/{country}/{source}/region-types",
    status_code=status.HTTP_200_OK,
)
async def get_region_types(
    source: ValidSource,
    country: CountryCode,
    auth: AuthDependency,
) -> list[RegionType]:
    """List available region types for a country."""
    _ = _energy_type_for(source)  # validate source
    cfg = _country_config(country)
    return [
        RegionType(
            type=rt.type,
            label=rt.label,
            level=rt.level,
            forecast_models=[
                ForecastModel(name=f.name, label=f.label) for f in rt.forecast_models
            ],
        )
        for rt in cfg.region_types
    ]


@router.get(
    "/{country}/{source}/generation-sources",
    status_code=status.HTTP_200_OK,
)
async def get_generation_sources(
    source: ValidSource,
    country: CountryCode,
    auth: AuthDependency,
) -> list[GenerationSource]:
    """List available generation types for a country."""
    _ = _energy_type_for(source)
    cfg = _country_config(country)

    sources = []
    for s in cfg.generation_sources:
        if s.source == source:
            sources.append(s)

    return sources


@router.get(
    "/{country}/{source}/regions",
    status_code=status.HTTP_200_OK,
)
async def get_country_regions(
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: str | None = Query(
        None,
        description="Filter by region type (e.g. 'gsp', 'dno', 'national').",
    ),
    parent_id: UUID | None = Query(
        None,
        description="List children of a specific region.",
    ),
) -> list[RegionDetail]:
    """List regions for a country, optionally filtered by type or parent.

    - No filters: returns all regions of all configured types.
    - ``?region_type=gsp``: only GSP regions.
    - ``?parent_id={uuid}``: children of a specific region.
    """
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    if parent_id is not None:
        # Children of a specific region — no type filter needed
        locs = await db.get_locations(
            energy_type=energy_type,
            location_type=None,
            authdata=auth,
            enclosing_location_uuid=parent_id,
        )
        return [_location_to_detail(loc, cfg) for loc in locs]

    if region_type is not None:
        # Filter by specific region type
        rt = cfg.get_region_type(region_type)
        if rt is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown region type '{region_type}' for {country.upper()}. "
                f"Available: {[r.type for r in cfg.region_types]}",
            )
        if rt.location_type == models.LocationType.NATION:
            # The national aggregate is the nation itself
            return [_location_to_detail(nation, cfg)]

        locs = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type,
            authdata=auth,
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )
        return [_location_to_detail(loc, cfg) for loc in locs]

    # No filters — combine all region types
    tasks = []
    for rt in cfg.region_types:
        if rt.location_type == models.LocationType.NATION:
            continue  # we already have the nation; add it directly below
        tasks.append(
            db.get_locations(
                energy_type=energy_type,
                location_type=rt.location_type,
                authdata=auth,
                enclosing_location_uuid=_to_uuid(nation.uuid),
            ),
        )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[RegionDetail] = [_location_to_detail(nation, cfg)]
    for result in results:
        if isinstance(result, Exception):
            raise result
        for loc in result:
            out.append(_location_to_detail(loc, cfg))
    return out


@router.get(
    "/{country}/{source}/regions/{region_id}",
    status_code=status.HTTP_200_OK,
)
async def get_region(
    source: ValidSource,
    country: CountryCode,
    region_id: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> RegionDetail:
    """Get details for a specific region."""
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
    return _location_to_detail(locs[0], cfg)


@router.get(
    "/{country}/{source}/regions/{region_id}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_region_forecast(
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
    resolved_id = await _resolve_region_id(region_id, cfg, energy_type, db)

    # Resolve the region to determine its LocationType
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
    model = model or (rt.default_model if rt else None)

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    pgvs = await db.get_predicted_generation(
        location_uuid=resolved_id,
        window_start=start_utc or now - dt.timedelta(days=2),
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
async def get_region_forecast_last_updated(
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
    model = model or (rt.default_model if rt else None)

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
    start_utc: dt.datetime | None = Query(
        None,
        description="Start of generation window (UTC).",
    ),
    end_utc: dt.datetime | None = Query(
        None,
        description="End of generation window (UTC).",
    ),
) -> GenerationResponse:
    """Get observed generation data for a specific region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    resolved_id = await _resolve_region_id(region_id, cfg, energy_type, db)

    # Resolve the region to determine its LocationType
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
            GenerationValue(
                time=v.valid_timestamp,
                power_kW=v.power_kilowatts,
            )
            for v in agvs
        ],
    )


@router.get(
    "/{country}/{source}/forecasts/snapshot",
    status_code=status.HTTP_200_OK,
)
@cache(key_builder=key_builder, expire=120)
async def get_forecasts_snapshot(
    request: Request,
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: str = Query(..., description="Region type (e.g. 'gsp', 'national')."),
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
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}.",
        )
    _validate_model(model_name, rt, rt.type)
    model_name = model_name or rt.default_model
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

    if location_type == models.LocationType.NATION:
        # Fallback: GetForecastAtTimestamp historically did not support NATION UUIDs.
        # Pull a ±30-min timeseries and pick the value nearest the snapshot time.
        pgvs = await db.get_predicted_generation(
            location_uuid=nation.uuid,
            window_start=snapshot_time - dt.timedelta(minutes=30),
            window_end=snapshot_time + dt.timedelta(minutes=30),
            energy_type=energy_type,
            location_type=models.LocationType.NATION,
            authdata={},  # TODO: add auth when loosed on DP side
            forecaster_name=model_name,
            forecaster_version=model_version,
        )
        snapshot = (
            [min(pgvs, key=lambda v: abs(v.valid_timestamp - snapshot_time))]
            if pgvs
            else []
        )
    else:
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
    "/{country}/{source}/forecasts/timeseries",
    status_code=status.HTTP_200_OK,
)
async def get_forecasts_timeseries(
    source: ValidSource,
    country: CountryCode,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    region_type: str = Query(..., description="Region type (e.g. 'gsp')."),
    start_utc: dt.datetime | None = Query(None, description="Start of window (UTC)."),
    end_utc: dt.datetime | None = Query(None, description="End of window (UTC)."),
    region_ids: list[UUID] | None = Query(
        None,
        description="Limit to specific region UUIDs.",
    ),
) -> ForecastMatrix:
    """Get forecast timeseries for all (or selected) regions across a time window.

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
    base = f"{prefix}:v1:timeseries:{country.upper()}:{source}:{region_type}"

    raw_meta = await backend.get(f"{base}:_meta")
    if raw_meta is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Forecast cache is being populated, please retry in 60 seconds.",
            headers={"Retry-After": "60"},
        )

    # Resolve the region list so we know which cache keys to read
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
    region_series: list[RegionForecastTimeSeries] = []
    for r, raw in zip(regions, raw_list, strict=True):
        if raw is None:
            continue
        all_values = [ForecastValue.model_validate(v) for v in json.loads(raw)]
        windowed = [v for v in all_values if win_start <= v.time <= win_end]
        region_series.append(
            RegionForecastTimeSeries(
                region_id=_to_uuid(r.uuid),
                capacity_kW=r.capacity_kilowatts,
                values=windowed,
            ),
        )

    metadata = json.loads(raw_meta)
    return ForecastMatrix(**metadata, regions=region_series)


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
        None,
        description="Observation target timestamp (UTC).",
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
    region_ids: list[UUID] | None = Query(
        None,
        description="Limit to specific region UUIDs.",
    ),
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
    region_type: str = Query("gsp", description="Region type to refresh."),
) -> Response:
    """Trigger a background re-warm of the forecast timeseries cache. Requires ocf:admin."""
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
