"""Cache warming logic and key builder for the v1 API."""

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import Request, Response
from fastapi_cache import FastAPICache

from quartz_api.internal import models

from .auth_scopes import ALL_COUNTRY_PERMISSIONS
from .country_config import COUNTRIES
from .helpers import internal_to_api_name, timeseries_window, to_uuid

log = logging.getLogger(__name__)


def _country_access_tier(auth: dict, country: object) -> str:
    """Reduce a user's permissions to the access tier for a specific country.

    Returns one of three values:
    - ``"full"``         — full access (read:gb, read:trial, read:partner, etc.)
    - ``"intraday"``     — intraday-only access (e.g. read:uk-intraday)
    - ``"unauthorized"`` — no access; route will 403 and response won't be cached

    All permission combinations that yield the same data tier share a single cache
    entry, so read:gb, read:trial, and read:partner users all hit the same key.
    """
    perms = frozenset(auth.get("permissions", []))
    if perms & ALL_COUNTRY_PERMISSIONS:
        return "full"
    permission = getattr(country, "permission", None)
    if permission and permission in perms:
        return "full"
    intraday_permission = getattr(country, "intraday_permission", None)
    if intraday_permission and intraday_permission in perms:
        return "intraday"
    return "unauthorized"


async def key_builder(
    func: Callable[..., Any],
    namespace: str = "",
    *,
    request: Request,
    response: Response,  # noqa: ARG001
    args: Any,  # noqa: ARG001, ANN401
    kwargs: Any,  # noqa: ANN401
) -> str:
    """Cache key builder for v1 routes.

    Uses the country access tier ("full" / "intraday" / "unauthorized") rather than
    raw permission strings so that all users who receive identical data share one
    cache entry. Unauthorized users always get a distinct key and therefore always
    reach the route handler (which raises 403, never cached).
    """
    auth = kwargs.get("auth", {})
    country = kwargs.get("country")
    tier = _country_access_tier(auth, country) if country is not None else None

    params = [
        (k, v)
        for k, v in request.query_params.items()
        if k in func.__code__.co_varnames
    ]
    parts = [namespace, request.method.lower(), request.url.path, repr(sorted(params))]
    if tier is not None:
        parts.append(tier)
    return ":".join(parts)


def forecast_period_base_key(
    prefix: str,
    country: str,
    source: str,
    region_type: str,
) -> str:
    """Return the base cache key for the forecast period cache."""
    return f"{prefix}:v1:period:{country.upper()}:{source}:{region_type}"


def generation_period_base_key(
    prefix: str,
    country: str,
    source: str,
    region_type: str,
    observer: str,
) -> str:
    """Return the base cache key for the generation period cache."""
    return f"{prefix}:v1:period:generation:{country.upper()}:{source}:{region_type}:{observer}"


# Per-combination warming flags: key is "{source}:{country}:{region_type}" or
# "{source}:{country}:{region_type}:{observer}" for generation.
forecast_cache_warming: dict[str, bool] = {}
generation_cache_warming: dict[str, bool] = {}


async def warm_v1_forecast_cache(
    app: object,
    energy_type: models.EnergyType,
    country: str,
    region_type: str,
) -> None:
    """Pre-warm per-region forecast timeseries cache for one (energy_type, country, region_type)."""
    flag_key = f"{energy_type.name.lower()}:{country}:{region_type}"
    forecast_cache_warming[flag_key] = True
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
        win_start, win_end = timeseries_window(None, None)

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
            enclosing_location_uuid=to_uuid(nation.uuid),
        )

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = forecast_period_base_key(
            prefix,
            country,
            energy_type.name.lower(),
            region_type,
        )
        first_found_pgv = None
        last_updated: dt.datetime | None = None
        latest_init: dt.datetime | None = None

        for i, region in enumerate(regions):
            pgvs = await db.get_predicted_generation(
                location_uuid=region.uuid,
                window_start=win_start,
                window_end=win_end,
                energy_type=energy_type,
                location_type=rt.location_type,
                authdata={},
                forecaster_name=rt.default_forecaster_name(),
            )
            if pgvs:
                if first_found_pgv is None:
                    first_found_pgv = pgvs[0]
                for pgv in pgvs:
                    if pgv.created_timestamp and (
                        last_updated is None or pgv.created_timestamp > last_updated
                    ):
                        last_updated = pgv.created_timestamp
                    if pgv.init_timestamp and (
                        latest_init is None or pgv.init_timestamp > latest_init
                    ):
                        latest_init = pgv.init_timestamp
            values = [
                {
                    "time_utc": v.valid_timestamp.isoformat(),
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
                    energy_type.name.lower(),
                    region_type,
                    i + 1,
                    len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        meta = {
            "model_name": internal_to_api_name(
                first_found_pgv.forecaster_name if first_found_pgv else None,
                rt,
            ),
            "model_version": (
                first_found_pgv.forecaster_version if first_found_pgv else None
            ),
            "last_updated_utc": last_updated.isoformat() if last_updated else None,
            "latest_init_utc": latest_init.isoformat() if latest_init else None,
            "cache_updated_utc": dt.datetime.now(tz=dt.UTC).isoformat(),
        }
        await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 forecast cache warmed: %s/%s/%s — %d regions",
            country,
            energy_type.name.lower(),
            region_type,
            len(regions),
        )
    except Exception:
        log.exception(
            "v1 forecast cache warm failed: %s/%s/%s",
            energy_type.name.lower(),
            country,
            region_type,
        )
    finally:
        forecast_cache_warming[flag_key] = False


async def warm_v1_generation_cache(
    app: object,
    energy_type: models.EnergyType,
    country: str,
    region_type: str,
    observer: str,
) -> None:
    """Pre-warm per-region generation timeseries cache for one combination."""
    flag_key = f"{energy_type.name.lower()}:{country}:{region_type}:{observer}"
    generation_cache_warming[flag_key] = True
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

        win_start, win_end = timeseries_window(None, None)

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
            enclosing_location_uuid=to_uuid(nation.uuid),
        )

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = generation_period_base_key(
            prefix,
            country,
            energy_type.name.lower(),
            region_type,
            observer,
        )
        gs = next((gs for gs in cfg.generation_sources if gs.name == observer), None)
        api_observer_name = gs.api_name if gs else observer

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
                    "time_utc": v.valid_timestamp.isoformat(),
                    "power_kW": v.power_kilowatts,
                }
                for v in agvs
            ]
            await backend.set(f"{base}:{region.uuid}", json.dumps(values), expire=86400)
            if (i + 1) % 20 == 0:
                log.info(
                    "v1 generation cache warm %s/%s/%s/%s: %d/%d regions",
                    energy_type.name.lower(),
                    country,
                    region_type,
                    observer,
                    i + 1,
                    len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        meta = {
            "observer_name": api_observer_name,
            "cache_updated_utc": dt.datetime.now(tz=dt.UTC).isoformat(),
        }
        await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 generation cache warmed: %s/%s/%s/%s — %d regions",
            energy_type.name.lower(),
            country,
            region_type,
            observer,
            len(regions),
        )
    except Exception:
        log.exception(
            "v1 generation cache warm failed: %s/%s/%s/%s",
            energy_type.name.lower(),
            country,
            region_type,
            observer,
        )
    finally:
        generation_cache_warming[flag_key] = False


async def warm_all_v1_caches(app: object) -> None:
    """Warm all v1 timeseries caches derived from the COUNTRIES config.

    Targets are inferred automatically: adding generation sources or a new country
    to country_config.py is sufficient to include them in the pre-warm.
    """
    await asyncio.sleep(5)
    tasks = []
    for country_code, cfg in COUNTRIES.items():
        for rt in cfg.region_types:
            if rt.location_type == models.LocationType.NATION:
                continue
            if rt.forecast_models:
                tasks.append(
                    warm_v1_forecast_cache(
                        app,
                        models.EnergyType.SOLAR,
                        country_code,
                        rt.type,
                    ),
                )
            for gen_src in cfg.generation_sources:
                tasks.append(
                    warm_v1_generation_cache(
                        app,
                        models.EnergyType.SOLAR,
                        country_code,
                        rt.type,
                        gen_src.name,
                    ),
                )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("v1 cache warm subtask failed", exc_info=r)
