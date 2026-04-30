"""Shared utilities for the v1 API router."""

import datetime as dt
from enum import StrEnum
from uuid import UUID

import pandas as pd
from fastapi import HTTPException
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .country_config import COUNTRIES, VALID_COUNTRY_CODES, CountryConfig, RegionTypeConfig
from .endpoint_types import RegionDetail, RegionSummary

# Derived at import time from country_config so Swagger renders a dropdown of valid values.
CountryCode = StrEnum("CountryCode", {k: k for k in COUNTRIES})


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


def _check_region_type(
    cfg: CountryConfig,
    region_type: str | None,
    country: str,
) -> RegionTypeConfig | None:
    """Validate region_type against config, raising 400 with available types if unknown."""
    if region_type is None:
        return None
    rt = cfg.get_region_type(region_type)
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown region type '{region_type}' for {country.upper()}. "
            f"Available: {[r.type for r in cfg.region_types]}",
        )
    return rt


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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
