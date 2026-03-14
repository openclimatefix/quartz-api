"""The v1 API router providing a unified view of regions, forecasts, and generation."""

# ruff: noqa: ARG001, B008

import asyncio
import datetime as dt
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, HTTPException, Query
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .country_config import COUNTRIES, VALID_COUNTRY_CODES, CountryConfig
from .endpoint_types import (
    ForecastModel,
    ForecastResponse,
    ForecastSnapshot,
    ForecastValue,
    GenerationResponse,
    GenerationSource,
    GenerationValue,
    RegionDetail,
    RegionForecastValue,
    RegionSummary,
    RegionType,
    Source,
    ValidObserver,
    ValidSource,
)

router = APIRouter(prefix="/v1", tags=["v1"])


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
    matches = [n for n in nations if n.name == country_cfg.nation_name]
    if len(matches) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No nation found with name '{country_cfg.nation_name}'.",
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
    "/{source}/regions",
    status_code=status.HTTP_200_OK,
)
async def get_top_level_regions(
    source: ValidSource,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> list[RegionDetail]:
    """List top-level regions (nations) for an energy source."""
    energy_type = _energy_type_for(source)
    nations = await db.get_locations(
        energy_type=energy_type,
        location_type=models.LocationType.NATION,
        authdata=auth,
    )
    return [
        RegionDetail(
            id=n.uuid,
            name=n.name,
            type="national",
            capacity_kW=n.capacity_kilowatts,
            latitude=n.latitude,
            longitude=n.longitude,
        )
        for n in nations
    ]


@router.get(
    "/{source}/{country}/region-types",
    status_code=status.HTTP_200_OK,
)
async def get_region_types(
    source: ValidSource,
    country: str,
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
                ForecastModel(name=f.name, label=f.label)
                for f in rt.forecast_models
            ],
        )
        for rt in cfg.region_types
    ]

@router.get(
    "/{source}/{country}/generation-sources",
    status_code=status.HTTP_200_OK,
)
async def get_generation_sources(
    source: ValidSource,
    country: str,
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
    "/{source}/{country}/regions",
    status_code=status.HTTP_200_OK,
)
async def get_country_regions(
    source: ValidSource,
    country: str,
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
            enclosing_location_uuid=UUID(nation.uuid)
            if isinstance(nation.uuid, str)
            else nation.uuid,
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
                enclosing_location_uuid=UUID(nation.uuid)
                if isinstance(nation.uuid, str)
                else nation.uuid,
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
    "/{source}/{country}/regions/{region_id}",
    status_code=status.HTTP_200_OK,
)
async def get_region(
    source: ValidSource,
    country: str,
    region_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> RegionDetail:
    """Get details for a specific region."""
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)

    locs = await db.get_locations(
        energy_type=energy_type,
        location_type=None,
        authdata={}, # TODO: add auth when loosed on DP side
        location_uuid=region_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{region_id}' not found.",
        )
    return _location_to_detail(locs[0], cfg)


@router.get(
    "/{source}/{country}/regions/{region_id}/forecast",
    status_code=status.HTTP_200_OK,
)
async def get_region_forecast(
    source: ValidSource,
    country: str,
    region_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    start_utc: dt.datetime | None = Query(
        None, description="Start of forecast window (UTC).",
    ),
    end_utc: dt.datetime | None = Query(
        None, description="End of forecast window (UTC).",
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
    model: str | None = Query(None, description="Forecast model name."),
) -> ForecastResponse:
    """Get the forecast for a specific region."""
    energy_type = _energy_type_for(source)
    _country_config(country)  # validate country

    # Resolve the region to determine its LocationType
    locs = await db.get_locations(
        energy_type=energy_type,
        location_type=None,
        authdata={}, # TODO: add auth when loosed on DP side
        location_uuid=region_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{region_id}' not found.",
        )
    region = locs[0]
    location_type = region.location_type or models.LocationType.NATION

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    pgvs = await db.get_predicted_generation(
        location_uuid=region_id,
        window_start=start_utc or now - dt.timedelta(days=2),
        window_end=end_utc or now + dt.timedelta(days=2),
        energy_type=energy_type,
        location_type=location_type,
        authdata={}, # TODO: add auth when loosed on DP side
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
    "/{source}/{country}/regions/{region_id}/generation",
    status_code=status.HTTP_200_OK,
)
async def get_region_generation(
    source: ValidSource,
    country: str,
    region_id: UUID,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    observer: ValidObserver = "pvlive_in_day",
    start_datetime_utc: dt.datetime | None = Query(
        None,
        description="Start of generation window (UTC).",
    ),
    end_datetime_utc: dt.datetime | None = Query(
        None,
        description="End of generation window (UTC).",
    ),
) -> GenerationResponse:
    """Get observed generation data for a specific region."""
    energy_type = _energy_type_for(source)
    _country_config(country)  # validate country

    # Resolve the region to determine its LocationType
    locs = await db.get_locations(
        energy_type=energy_type,
        location_type=None,
        authdata={}, # TODO: add auth when loosed on DP side
        location_uuid=region_id,
    )
    if len(locs) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Region '{region_id}' not found.",
        )
    region = locs[0]
    location_type = region.location_type or models.LocationType.NATION

    now = pd.Timestamp.utcnow().floor("h").to_pydatetime()
    agvs = await db.get_actual_generation(
        location_uuid=region_id,
        window_start=start_datetime_utc or now - dt.timedelta(days=5),
        window_end=end_datetime_utc or now,
        energy_type=energy_type,
        location_type=location_type,
        observer_name=observer,
        authdata={}, # TODO: add auth when loosed on DP side
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
    "/{source}/{country}/forecasts",
    status_code=status.HTTP_200_OK,
)
async def get_forecasts_snapshot(
    source: ValidSource,
    country: str,
    db: models.StorageClientDependency,
    auth: AuthDependency,
    model: str | None = Query(None, description="Forecast model name."),
    region_type: str | None = Query(
        None,
        description="Filter regions by type (e.g. 'gsp').",
    ),
    timestamp: dt.datetime | None = Query(
        None,
        description="Forecast target timestamp (UTC).",
    ),
) -> ForecastSnapshot:
    """Get forecasts for all regions of a given type at a specific timestamp.

    Used for retrieving the latest forecast snapshot across many regions.
    """
    energy_type = _energy_type_for(source)
    cfg = _country_config(country)
    nation = await _resolve_nation(db, energy_type, cfg, auth)

    # Determine which location type to query
    if region_type is not None:
        rt = cfg.get_region_type(region_type)
        if rt is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown region type '{region_type}' for {country.upper()}.",
            )
        location_type = rt.location_type
    else:
        # Default to GSP if no type specified
        location_type = models.LocationType.GSP

    # Get all regions of the requested type
    if location_type == models.LocationType.NATION:
        regions = [nation]
    else:
        regions = await db.get_locations(
            energy_type=energy_type,
            location_type=location_type,
            authdata=auth,
            enclosing_location_uuid=UUID(nation.uuid)
            if isinstance(nation.uuid, str)
            else nation.uuid,
        )

    if len(regions) == 0:
        return []

    snapshot_time = timestamp or pd.Timestamp.utcnow().floor("30min").to_pydatetime()
    snapshot = await db.get_predicted_generation_snapshot(
        location_uuids=[
            UUID(r.uuid) if isinstance(r.uuid, str) else r.uuid for r in regions
        ],
        forecaster_name=model,
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
