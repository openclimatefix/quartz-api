"""Discovery routes — sources, countries, region types, and generation sources."""

# ruff: noqa: ARG001

from fastapi import APIRouter, Request
from fastapi_cache.decorator import cache
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency
from ..cache import key_builder

from ..country_config import COUNTRIES
from ..endpoint_types import (
    Centroid,
    CountryDetail,
    CountryParam,
    ForecastModel,
    GenerationSource,
    RegionType,
    Source,
    ValidSource,
)

router = APIRouter(tags=["Discovery"])


@router.get("/sources", status_code=status.HTTP_200_OK)
@cache(key_builder=key_builder, expire=60)
async def get_sources(
    request: Request,
    auth: AuthDependency,
) -> list[Source]:
    """List available forecast energy sources.

    Returns the set of energy source types supported by the API. Use the `name` value
    as the `{source}` path segment in all other v1 routes (e.g. `/v1/GB/solar/regions`).
    Cached for 1 minute.
    """
    return [
        Source(name="solar", label="Solar"),
    ]


@router.get("/countries", status_code=status.HTTP_200_OK)
@cache(key_builder=key_builder, expire=60)
async def get_countries(
    request: Request,
    db: models.StorageClientDependency,
    auth: AuthDependency,
) -> list[CountryDetail]:
    """List available countries with their full capability manifest.

    Returns one entry per country the API has data for. Each entry includes:

    - **region_types** — the region granularities available for that country (e.g. national,
      GSP), including the forecast models valid for each type.
    - **generation_sources** — the observed-generation observers available (e.g.
      `pvlive_in_day`, `pvlive_day_after`).
    - **capacity_kW** and **centroid** — installed capacity and geographic centre.

    Use the `country` value (e.g. `GB`, `NL`) as the `{country}` path segment in all other
    v1 routes. Cached for 1 minute.
    """
    nations = await db.get_locations(
        energy_type=models.EnergyType.SOLAR,
        location_type=models.LocationType.NATION,
        authdata={},
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
                name=cfg.display_name,
                capacity_kW=nation.capacity_kilowatts,
                centroid=Centroid(lat=nation.latitude, lng=nation.longitude),
                region_types=[
                    RegionType(
                        type=rt.type,
                        label=rt.label,
                        level=rt.level,
                        default_model=rt.default_model_api_name(),
                        forecast_models=[
                            ForecastModel(name=f.api_name, label=f.label)
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


@router.get("/{country}/{source}/region-types", status_code=status.HTTP_200_OK)
@cache(key_builder=key_builder, expire=60)
async def get_region_types(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    auth: AuthDependency,
) -> list[RegionType]:
    """List available region types for a country and energy source.

    Region types define the geographic granularity at which forecasts and generation
    data are available — for example `national` or `gsp` for Great Britain.

    Each entry includes:

    - **type** — the slug used as the `region_type` query parameter throughout the API.
    - **level** — hierarchical depth (lower = coarser; 0 = national).
    - **forecast_models** — the models available for that region type, with the model
      `name` used as the `model` parameter on forecast endpoints. The first listed model
      is the default.

    Cached for 1 minute.
    """
    return [
        RegionType(
            type=rt.type,
            label=rt.label,
            level=rt.level,
            default_model=rt.default_model,
            forecast_models=[
                ForecastModel(name=f.api_name, label=f.label)
                for f in rt.forecast_models
            ],
        )
        for rt in country.region_types
    ]


@router.get("/{country}/{source}/generation-sources", status_code=status.HTTP_200_OK)
@cache(key_builder=key_builder, expire=60)
async def get_generation_sources(
    request: Request,
    source: ValidSource,
    country: CountryParam,
    auth: AuthDependency,
) -> list[GenerationSource]:
    """List available observed-generation sources for a country and energy source.

    Generation sources represent the different observers that produce actual (measured)
    generation data — for example PV_Live in-day estimates vs finalised day-after values.
    Use the `name` field as the `observer` parameter on generation endpoints.
    Cached for 1 minute.
    """
    return [s for s in country.generation_sources if s.source == source.name.lower()]
