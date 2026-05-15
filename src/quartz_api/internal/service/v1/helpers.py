"""Shared utilities for the v1 API router."""

# ruff: noqa: ARG001

import datetime as dt
from uuid import UUID

import pandas as pd
from fastapi import HTTPException
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .auth_scopes import ALL_COUNTRY_PERMISSIONS
from quartz_api.constants import SUPPORT_EMAIL
from .country_config import (
    CountryConfig,
    RegionTypeConfig,
)
from .endpoint_types import Centroid, RegionDetail, RegionSummary


async def resolve_nation(
    db: models.StorageInterface,
    energy_type: models.EnergyType,
    country_cfg: CountryConfig,
    auth: AuthDependency,
) -> models.Location:
    """Resolve a country config to its nation Location in the data platform."""
    nations = await db.get_locations(
        energy_type=energy_type,
        location_type=models.LocationType.NATION,
        authdata={},
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
        name=location_display_name(loc, country_cfg),
        type=region_type_name,
        capacity_kW=loc.capacity_kilowatts,
        centroid=Centroid(lat=loc.latitude, lng=loc.longitude),
    )


def location_display_name(loc: models.Location, country_cfg: CountryConfig) -> str:
    """Return the user-facing name for a location.

    Resolution order:
    1. NATION → country display_name
    2. RegionTypeConfig.location_name_map → mapped display name
    3. loc.name unchanged
    """
    if loc.location_type == models.LocationType.NATION:
        return country_cfg.display_name
    if loc.location_type is not None:
        rt = country_cfg.location_type_to_region_type(loc.location_type)
        if rt is not None:
            mapped = rt.get_display_name(loc.name)
            if mapped is not None:
                return mapped
    return loc.name


def location_to_detail(
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
        name=location_display_name(loc, country_cfg),
        type=rt.type if rt else None,
        capacity_kW=loc.capacity_kilowatts,
        centroid=Centroid(lat=loc.latitude, lng=loc.longitude),
        metadata={k: v for k, v in loc.metadata.items() if k in allowed},
    )


def to_uuid(val: str | UUID) -> UUID:
    """Convert a string or UUID to UUID."""
    return UUID(val) if isinstance(val, str) else val


def check_region_type(
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


def validate_model(
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


async def resolve_region_id(
    region_id: str,
    cfg: CountryConfig,
    energy_type: models.EnergyType,
    db: models.StorageInterface,
) -> UUID:
    """Resolve a region path param to an internal UUID.

    Resolution order:
    1. "national" slug → nation UUID
    2. Nation display name or internal name → nation UUID
    3. Mapped display name (e.g. "friesland") → reverse-lookup to DP internal name, then search
    4. Anything else → case-insensitive name search across all region types
    """
    try:
        return UUID(region_id)
    except ValueError:
        pass

    # Need the nation for both "national" resolution and name search.
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"National region for '{cfg.nation_name}' not found.",
        )

    if region_id == "national":
        return nation.uuid

    # Name search — check nation aliases first.
    needle = region_id.lower()
    if needle in (nation.name.lower(), cfg.display_name.lower()):
        return nation.uuid

    # Reverse-lookup mapped display names → DP internal name.
    # e.g. "friesland" → "nl_region_2_friesland" for NL provinces.
    dp_name: str | None = None
    for rt in cfg.region_types:
        for internal, display in rt.location_name_map:
            if display.lower() == needle:
                dp_name = internal
                break
        if dp_name is not None:
            break

    search_name = dp_name or region_id
    locs = await db.get_locations(
        energy_type=energy_type,
        location_type=None,
        authdata={},
        enclosing_location_uuid=to_uuid(nation.uuid),
        location_names=[search_name],
    )
    # Client-side confirmation: DP may not filter by name server-side yet.
    match = next((loc for loc in locs if loc.name.lower() == search_name.lower()), None)
    if match is not None:
        return match.uuid
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Region '{region_id}' not found.",
    )


def _get_permissions(auth: AuthDependency) -> frozenset[str]:
    perms = auth.get("permissions", [])
    if isinstance(perms, str):
        perms = [perms]
    return frozenset(perms)


def check_country_access(auth: AuthDependency, cfg: CountryConfig) -> bool:
    """Check country-level access. Returns True for full access, False for intraday-only.

    Three-tier check (short-circuits on first match):
      1. ALL_COUNTRY_PERMISSIONS (read:trial, read:partner) → full access to every country
      2. cfg.permission (e.g. read:gb) → full access to this country
      3. cfg.intraday_permission (e.g. read:uk-intraday) → intraday models only (False)
      No match → HTTP 403

    Callers store the bool as `is_intraday_only = not _check_country_access(...)` and
    pass it to `_resolve_forecast_model` to apply model restrictions.
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
            f"Contact {SUPPORT_EMAIL} to request access."
        ),
    )


def resolve_forecast_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    is_intraday_only: bool,
) -> str | None:
    """Validate and resolve the forecast model, returning the internal DP name.

    Some models have a user-facing slug (api_name) that differs from the internal DP
    forecaster_name — e.g. pvnet_v2 is exposed as "pvnet_intraday".  This function:
      1. Picks the default model if none was requested (intraday default for restricted users)
      2. Validates intraday-only users can only request intraday models (HTTP 403 otherwise)
      3. Translates the slug back to the raw DP forecaster_name before the backend call
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
        resolved_api = model or (
            rt.intraday_default_model.api_name if rt.intraday_default_model else None
        )
    else:
        _default_fm = (
            rt.get_model_by_internal_name(rt.default_model)
            if rt.default_model
            else None
        )
        resolved_api = model or (
            _default_fm.api_name if _default_fm else rt.default_model
        )
    # Translate api_name → internal DP name before the backend call.
    if resolved_api is not None:
        fm = rt.get_model_by_api_name(resolved_api)
        return fm.name if fm else resolved_api
    return None


def internal_to_api_name(
    internal_name: str | None,
    rt: RegionTypeConfig | None,
) -> str | None:
    """Translate an internal DP forecaster name to its user-facing API slug."""
    if internal_name is None or rt is None:
        return internal_name
    fm = rt.get_model_by_internal_name(internal_name)
    return fm.api_name if fm else internal_name


def timeseries_window(
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


_MAX_WINDOW = dt.timedelta(days=92)  # ~3 months — enforced on our side before DP
_DP_CHUNK = dt.timedelta(days=7)  # DP per-call limit


def validate_window(start: dt.datetime, end: dt.datetime) -> None:
    """Raise 400 if start >= end or the window exceeds the 3-month limit."""
    if start >= end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"start_utc must be before end_utc "
                f"(got {start.isoformat()} >= {end.isoformat()})."
            ),
        )
    if end - start > _MAX_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Requested window of {(end - start).days} days exceeds the 3-month limit "
                f"({_MAX_WINDOW.days} days). Split into smaller requests if you need more history."
            ),
        )


def window_chunks(
    start: dt.datetime,
    end: dt.datetime,
) -> list[tuple[dt.datetime, dt.datetime]]:
    """Split a window into _DP_CHUNK-sized sub-windows for sequential DP calls."""
    chunks = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + _DP_CHUNK, end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end
    return chunks
