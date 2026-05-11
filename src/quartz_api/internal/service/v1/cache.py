"""Cache warming logic for the v1 API timeseries endpoints."""

import asyncio
import json
import logging

from fastapi_cache import FastAPICache

from quartz_api.internal import models

from .country_config import COUNTRIES
from .helpers import _timeseries_window, _to_uuid

log = logging.getLogger(__name__)

# Per-combination warming flags: key is "{source}:{country}:{region_type}" or
# "{source}:{country}:{region_type}:{observer}" for generation.
_forecast_cache_warming: dict[str, bool] = {}
_generation_cache_warming: dict[str, bool] = {}


async def _warm_v1_forecast_cache(
    app: object,
    energy_type: models.EnergyType,
    country: str,
    region_type: str,
) -> None:
    """Pre-warm per-region forecast timeseries cache for one (energy_type, country, region_type)."""
    flag_key = f"{energy_type.name.lower()}:{country}:{region_type}"
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
            log.warning("v1 forecast cache warm: nation '%s' not found", cfg.nation_name)
            return

        regions = await db.get_locations(
            energy_type=energy_type,
            location_type=rt.location_type,
            authdata={},
            enclosing_location_uuid=_to_uuid(nation.uuid),
        )

        backend = FastAPICache.get_backend()
        prefix = FastAPICache.get_prefix()
        base = f"{prefix}:v1:timeseries:{country.upper()}:{energy_type.name.lower()}:{region_type}"
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
                    country, energy_type.name.lower(), region_type, i + 1, len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        created = first_pgv.created_timestamp if first_pgv else None
        init = first_pgv.init_timestamp if first_pgv else None
        meta = {
            "model_name": first_pgv.forecaster_name if first_pgv else None,
            "model_version": first_pgv.forecaster_version if first_pgv else None,
            "created_time": created.isoformat() if created else None,
            "init_time": init.isoformat() if init else None,
        }
        await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 forecast cache warmed: %s/%s/%s — %d regions",
            country, energy_type.name.lower(), region_type, len(regions),
        )
    except Exception:
        log.exception(
            "v1 forecast cache warm failed: %s/%s/%s",
            energy_type.name.lower(), country, region_type,
        )
    finally:
        _forecast_cache_warming[flag_key] = False


async def _warm_v1_generation_cache(
    app: object,
    energy_type: models.EnergyType,
    country: str,
    region_type: str,
    observer: str,
) -> None:
    """Pre-warm per-region generation timeseries cache for one combination."""
    flag_key = f"{energy_type.name.lower()}:{country}:{region_type}:{observer}"
    _generation_cache_warming[flag_key] = True
    try:
        db = app.dependency_overrides.get(models.get_storage_client, lambda: None)()
        if db is None:
            log.warning("v1 generation cache warm skipped: storage client not configured")
            return

        cfg = COUNTRIES.get(country.upper())
        if cfg is None:
            return
        rt = cfg.get_region_type(region_type)
        if rt is None:
            return

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
                "v1 generation cache warm: nation '%s' not found", cfg.nation_name,
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
            f":{country.upper()}:{energy_type.name.lower()}:{region_type}:{observer}"
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
                    energy_type.name.lower(), country, region_type, observer, i + 1, len(regions),
                )
            # yield to event loop; keeps live requests responsive during warm
            await asyncio.sleep(0.1)

        meta = {"observer_name": observer}
        await backend.set(f"{base}:_meta", json.dumps(meta), expire=86400)

        log.info(
            "v1 generation cache warmed: %s/%s/%s/%s — %d regions",
            energy_type.name.lower(), country, region_type, observer, len(regions),
        )
    except Exception:
        log.exception(
            "v1 generation cache warm failed: %s/%s/%s/%s",
            energy_type.name.lower(), country, region_type, observer,
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
        for rt in cfg.region_types:
            if rt.location_type == models.LocationType.NATION:
                continue
            if rt.forecast_models:
                tasks.append(
                    _warm_v1_forecast_cache(app, models.EnergyType.SOLAR, country_code, rt.type),
                )
            for gen_src in cfg.generation_sources:
                tasks.append(
                    _warm_v1_generation_cache(
                        app, models.EnergyType.SOLAR, country_code, rt.type, gen_src.name,
                    ),
                )
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("v1 cache warm subtask failed", exc_info=r)
