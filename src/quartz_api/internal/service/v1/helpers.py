"""Shared utilities for the v1 API router."""

import datetime as dt
from enum import StrEnum
from uuid import UUID

import pandas as pd
from fastapi import HTTPException
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .auth_scopes import ALL_COUNTRY_PERMISSIONS
from .country_config import (
    COUNTRIES,
    VALID_COUNTRY_CODES,
    CountryConfig,
    RegionTypeConfig,
)
from .endpoint_types import Centroid, RegionDetail, RegionSummary

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
        centroid=Centroid(lat=loc.latitude, lng=loc.longitude),
    )


def _location_to_detail(
    loc: models.Location,
    country_cfg: CountryConfig,
) -> RegionDetail:
    """Convert an internal Location to a RegionDetail."""
    rt = (
        country_cfg.location_type_to_region_type(loc.location_type)
        if loc.location_type
        else None
    )
    # Filter for explicitly permitted properties
    allowed = rt.metadata_fields if rt else ()
    return RegionDetail(
        id=loc.uuid,
        name=loc.name,
        type=rt.type if rt else None,
        capacity_kW=loc.capacity_kilowatts,
        latitude=loc.latitude,
        longitude=loc.longitude,
        centroid=Centroid(lat=loc.latitude, lng=loc.longitude),
        metadata={k: v for k, v in loc.metadata.items() if k in allowed},
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


_MAX_WINDOW_DAYS = 365


def _validate_window(start_utc: dt.datetime | None) -> None:
    """Raise 400 if start_utc is more than one rolling year in the past."""
    if start_utc is None:
        return
    earliest = dt.datetime.now(tz=dt.UTC) - dt.timedelta(days=_MAX_WINDOW_DAYS)
    if start_utc.tzinfo is None:
        start_utc = start_utc.replace(tzinfo=dt.UTC)
    if start_utc < earliest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "start_utc exceeds the 1-year rolling data limit. "
                "Contact us at quartz@openclimatefix.org for access to extended history."
            ),
        )


def _validate_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    region_type_label: str,
) -> None:
    """Raise 400 if model is provided but not listed for the region type."""
    if model is None or rt is None or not rt.forecast_models:
        return
    valid = {f.api_name for f in rt.forecast_models}
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


def _get_permissions(auth: AuthDependency) -> frozenset[str]:
    perms = auth.get("permissions", [])
    if isinstance(perms, str):
        perms = [perms]
    return frozenset(perms)


def _check_country_access(auth: AuthDependency, cfg: CountryConfig) -> bool:
    """Check country-level access. Returns True for full access, False for intraday-only.

    Raises HTTP 403 if the user has no valid permission for this country.
    """
    perms = _get_permissions(auth)
    if perms & ALL_COUNTRY_PERMISSIONS:
        return True
    if cfg.permission and cfg.permission in perms:
        return True
    if cfg.intraday_permission and cfg.intraday_permission in perms:
        return False
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"You do not have access to {cfg.permission or 'this country'}. "
            "Contact quartz@openclimatefix.org to request access."
        ),
    )


def _resolve_forecast_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    is_intraday_only: bool,
) -> str | None:
    """Validate and resolve the forecast model, returning the internal DP name.

    Accepts user-facing API names (slugs) and translates to internal names for the DP.
    Applies intraday model restrictions for intraday-only users.
    """
    if rt is None:
        return model
    if is_intraday_only and rt.intraday_models:
        allowed = rt.intraday_api_names()
        if model is not None and model not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Model '{model}' is not available with your current access level. "
                    f"Intraday-accessible models: {sorted(allowed)}"
                ),
            )
        _default_fm = (
            rt.get_model_by_internal_name(rt.intraday_default_model)
            if rt.intraday_default_model
            else None
        )
        resolved_api = model or (
            _default_fm.api_name if _default_fm else rt.intraday_default_model
        )
    else:
        _default_fm = (
            rt.get_model_by_internal_name(rt.default_model) if rt.default_model else None
        )
        resolved_api = model or (_default_fm.api_name if _default_fm else rt.default_model)
    # Translate api_name → internal DP name before the backend call.
    if resolved_api is not None:
        fm = rt.get_model_by_api_name(resolved_api)
        return fm.name if fm else resolved_api
    return None


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
