"""Discovery routes — sources, countries, region types, and generation sources."""

# ruff: noqa: ARG001

from fastapi import APIRouter
from starlette import status

from quartz_api.internal import models
from quartz_api.internal.middleware.auth import AuthDependency

from ..country_config import COUNTRIES
from ..endpoint_types import (
    Centroid,
    CountryDetail,
    ForecastModel,
    GenerationSource,
    RegionType,
    Source,
    ValidSource,
)
from ..helpers import CountryCode, _country_config, _energy_type_for

router = APIRouter(tags=["Discovery"])


@router.get("/sources", status_code=status.HTTP_200_OK)
async def get_sources(
    auth: AuthDependency,
) -> list[Source]:
    """List available forecast energy sources."""
    return [
        Source(name="solar", label="Solar"),
        Source(name="wind", label="Wind"),
    ]


@router.get("/countries", status_code=status.HTTP_200_OK)
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
                centroid=Centroid(lat=nation.latitude, lng=nation.longitude),
                region_types=[
                    RegionType(
                        type=rt.type,
                        label=rt.label,
                        level=rt.level,
                        default_model=rt.default_model,
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


@router.get("/{country}/{source}/region-types", status_code=status.HTTP_200_OK)
async def get_region_types(
    source: ValidSource,
    country: CountryCode,
    auth: AuthDependency,
) -> list[RegionType]:
    """List available region types for a country."""
    _ = _energy_type_for(source)
    cfg = _country_config(country)
    return [
        RegionType(
            type=rt.type,
            label=rt.label,
            level=rt.level,
            default_model=rt.default_model,
            forecast_models=[
                ForecastModel(name=f.name, label=f.label) for f in rt.forecast_models
            ],
        )
        for rt in cfg.region_types
    ]


@router.get("/{country}/{source}/generation-sources", status_code=status.HTTP_200_OK)
async def get_generation_sources(
    source: ValidSource,
    country: CountryCode,
    auth: AuthDependency,
) -> list[GenerationSource]:
    """List available generation sources for a country."""
    _ = _energy_type_for(source)
    cfg = _country_config(country)
    return [s for s in cfg.generation_sources if s.source == source]
