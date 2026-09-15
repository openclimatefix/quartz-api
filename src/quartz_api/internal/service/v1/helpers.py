"""Shared utilities for the v1 API router."""

# ruff: noqa: ARG001

import contextlib
import datetime as dt
import json
from uuid import UUID

import pandas as pd
from fastapi import HTTPException
from starlette import status

from quartz_api.constants import SUPPORT_EMAIL
from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from .auth_scopes import ALL_COUNTRY_PERMISSIONS
from .country_config import (
    CountryConfig,
    ForecastModel,
    RegionTypeConfig,
)
from .endpoint_types import Centroid, RegionDetail, RegionSummary

# Sorts after every numbered level, so an unrecognised key is kept, not dropped.
_PLEVEL_UNKNOWN = 10_000


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
    metadata: dict = {k: v for k, v in loc.metadata.items() if k in allowed}
    installed = installed_capacity_kw(loc)
    if installed is not None:
        metadata["installed_capacity_kW"] = installed
    return RegionDetail(
        name=location_display_name(loc, country_cfg),
        type=rt.type if rt else None,
        capacity_kW=loc.capacity_kilowatts,
        centroid=Centroid(lat=loc.latitude, lng=loc.longitude),
        metadata=metadata,
    )


def installed_capacity_kw(loc: models.Location) -> float | None:
    """Return the location's capacity before degradation, if the platform has one.

    This is what v0 reported as `installedCapacityMw` and what PV Live publishes. It is
    not what the forecast is normalised against — `capacity_kW` is — and it is a few
    percent higher, so it is kept out of the top level and named explicitly. Returns
    `None` where the platform has no such figure, rather than quietly falling back to
    the effective capacity and reporting one number as the other.
    """
    raw = loc.metadata.get("capacity_no_degradation_kw")
    return float(raw) if isinstance(raw, (int, float)) else None


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


def resolve_model_param(
    model_name: str | None,
    model: str | None,
) -> str | None:
    """Collapse the current `model_name` param and its deprecated `model` alias.

    `model` was the original name on the per-region forecast routes; `model_name` is
    the name used everywhere now, alongside `model_version`. Both are accepted, but
    supplying both with different values is a 400 rather than a silent preference.
    """
    if model is None:
        return model_name
    if model_name is None:
        return model
    if model_name != model:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Conflicting values for 'model_name' ({model_name!r}) and its deprecated "
                f"alias 'model' ({model!r}). Supply only 'model_name'."
            ),
        )
    return model_name


def validate_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    region_type_label: str,
) -> None:
    """Raise 400 if model is provided but not listed for the region type."""
    if model is None or rt is None or not rt.forecast_models:
        return
    if rt.get_model_by_api_name(model) is None:
        # Only current names are advertised — retired aliases resolve but stay unlisted.
        valid = {f.api_name for f in rt.forecast_models}
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


def _default_forecast_model(
    rt: RegionTypeConfig,
    is_intraday_only: bool,
) -> ForecastModel | None:
    """The model to use when the caller named none, or None if none is configured."""
    if is_intraday_only and rt.intraday_models:
        # Naming nothing must not yield a model naming it would 403 on.
        return rt.intraday_default_model or rt.intraday_models[0]
    if rt.default_model is None:
        return None
    return rt.get_model_by_internal_name(rt.default_model)


def resolve_forecast_model(
    model: str | None,
    rt: RegionTypeConfig | None,
    is_intraday_only: bool,
    adjusted: bool = True,
) -> str | None:
    """Resolve a user-facing model name to the internal DP forecaster_name.

    User-facing names are slugs that differ from the DP forecaster_name — pvnet_v2 is
    exposed as "ecmwf_mo_sat_8h" — and retired slugs are still accepted as aliases.
    Raises 403 if an intraday-only caller names a model outside their permitted set.
    """
    if rt is None:
        return model

    if model is None:
        fm = _default_forecast_model(rt, is_intraday_only)
        if fm is None:
            # None sends no forecaster_name, so the DP selects the forecaster.
            return rt.default_model
        return fm.internal_name(adjusted=adjusted and rt.supports_adjusted)

    fm = rt.get_model_by_api_name(model)
    if fm is None:
        # Only reachable for a region type with no configured models — every route
        # calls validate_model first, which 400s an unknown name against a real list.
        return model
    if is_intraday_only and rt.intraday_models and fm not in rt.intraday_models:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Model '{model}' is not available with your current access level. "
                f"Intraday-accessible models: {sorted(rt.intraday_api_names())}"
            ),
        )
    # A retired alias pins its own adjuster state, keeping its pre-rename result.
    override = fm.alias_adjust_override(model)
    use_adjusted = adjusted if override is None else override
    return fm.internal_name(adjusted=use_adjusted and rt.supports_adjusted)


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


def latest_run_timestamps(
    values: list,
) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Return the latest `created_timestamp` / `init_timestamp` across forecast values.

    The data platform stitches the best-available (latest-run) value for each target
    time, so a response spans many model runs. Taking the maximum makes
    `last_updated_utc` mean "the most recent run contributing to this response" rather
    than "whichever value the platform happened to return first".
    """
    created = [v.created_timestamp for v in values if v.created_timestamp]
    init = [v.init_timestamp for v in values if v.init_timestamp]
    return (max(created) if created else None, max(init) if init else None)


def latest_capacity(values: list) -> float:
    """Return the capacity of the value with the latest `valid_timestamp`.

    The platform gives no ordering guarantee, so taking `values[0]` made this depend on
    which value came back first. Effective capacity is time-varying, so a single hoisted
    number cannot express a mid-window change either way — but the latest value's
    capacity at least applied inside the requested window, and is reproducible.
    """
    if not values:
        return 0.0
    return max(values, key=lambda v: v.valid_timestamp).capacity_kilowatts


def parse_forecast_metadata(metadata: dict) -> dict | None:
    """Return the forecaster's metadata dict with `app_version` parsed into an object.

    The pipeline stringifies `app_version` — for `blend` it is a JSON object encoded as
    a string — so returning it verbatim would make clients parse twice. Everything else
    passes through as the forecaster wrote it: the keys vary by model and are not a
    stable contract, pending a single pipeline that makes them predictable. If/when they
    are in future, we can revisit this.
    """
    if not metadata:
        return None
    parsed = dict(metadata)
    raw = parsed.get("app_version")
    if isinstance(raw, str):
        with contextlib.suppress(json.JSONDecodeError):
            parsed["app_version"] = json.loads(raw)
    return parsed


def plevel_sort_key(name: str) -> tuple[int, str]:
    """Order a probability level by its percentile, so p10 precedes p90.

    A name that is not `p<digits>` sorts after the numbered ones rather than raising,
    so an unknown future key from the DP still comes back.
    """
    digits = name[1:] if name[:1].lower() == "p" else name
    return (int(digits), name) if digits.isdigit() else (_PLEVEL_UNKNOWN, name)


def sort_plevels(plevels: dict) -> dict:
    """Return the probability levels ordered from lowest percentile to highest.

    The DP hands these over as a map with no ordering guarantee. JSON objects
    keep insertion order, so ordering them here is what the caller sees.
    """
    return {k: plevels[k] for k in sorted(plevels, key=plevel_sort_key)}


def region_metadata(loc: models.Location, detail: object) -> dict | None:
    """Region-level extras for a time-series wrapper, only at the `full` detail level.

    Installed capacity is deliberately awkward to reach: it is not the number the
    forecast uses, so a caller has to ask for it by name rather than meet it by default.
    """
    if str(detail) != "full":
        return None
    installed = installed_capacity_kw(loc)
    return {"installed_capacity_kW": installed} if installed is not None else None
