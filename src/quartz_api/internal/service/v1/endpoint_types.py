"""Pydantic models for v1 API request/response types."""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import Path, Query
from pydantic import BaseModel, BeforeValidator, Field, field_validator

from quartz_api.internal import models

from .country_config import COUNTRIES


def _get_forecast_model_names() -> tuple[str, ...]:
    """Extract all unique forecast model API names (slugs) from country configs."""
    names: set[str] = set()
    for country_cfg in COUNTRIES.values():
        for rt in country_cfg.region_types:
            for m in rt.forecast_models:
                names.add(m.api_name)
    return tuple(sorted(names))


# ValidForecastModel and ValidRegionType are evaluated once at import time so that
# Swagger/Scalar renders a static dropdown of valid values.  The enum spans all
# countries — per-country validation happens inside the route handlers.
ValidForecastModel = Annotated[
    str,
    Query(
        description=(
            "Forecast model name. If omitted, the default model for the region type is used "
            "(see `/{country}/{source}/region-types`)."
        ),
        enum=list(_get_forecast_model_names()),
    ),
]


def _get_region_type_names() -> tuple[str, ...]:
    """Extract all unique region type slugs from country configs."""
    names: set[str] = set()
    for country_cfg in COUNTRIES.values():
        for rt in country_cfg.region_types:
            names.add(rt.type)
    return tuple(sorted(names))


ValidRegionType = Annotated[
    str,
    Query(
        description=(
            "Region type slug (e.g. 'gsp', 'national'). "
            "Valid values are country-specific — see `/{country}/{source}/region-types`. "
            "The enum lists all types across all countries."
        ),
        enum=list(_get_region_type_names()),
    ),
]


def _get_observer_sources() -> tuple[str, ...]:
    """Extract all unique observer source names from country configs."""
    sources = set()
    for country_cfg in COUNTRIES.values():
        for gen_type in country_cfg.generation_sources:
            sources.add(gen_type.name)
    return tuple(sorted(sources))


def _build_observer_pattern() -> str:
    """Build regex pattern from available observer sources."""
    sources = _get_observer_sources()
    if not sources:
        return "^$"  # fallback if empty
    return f"^({'|'.join(sources)})$"


def _parse_source(v: str) -> models.EnergyType:
    if v == "solar":
        return models.EnergyType.SOLAR
    return models.EnergyType.WIND


ValidSource = Annotated[
    models.EnergyType,
    BeforeValidator(_parse_source),
    Path(
        description="The energy source type.",
        enum=["solar", "wind"],
        examples=["solar"],
    ),
]

ValidObserver = Annotated[
    str,
    Query(
        description="The observer source name.",
        pattern=_build_observer_pattern(),
        examples=list(_get_observer_sources()),
        enum=list(_get_observer_sources()),
    ),
]


class Centroid(BaseModel):
    """Geographic centroid of a region."""

    lat: float
    lng: float

    @field_validator("lat", "lng")
    @classmethod
    def _round_3dp(cls, v: float) -> float:
        return round(v, 3)


class Source(BaseModel):
    """An available forecast source (energy type)."""

    name: str
    label: str


class ForecastModel(BaseModel):
    """A forecaster (model) available for a region type."""

    name: str
    label: str


class GenerationSource(BaseModel):
    """A generation (observation) source definition for a country."""

    source: str
    name: str
    label: str


class RegionType(BaseModel):
    """A region type definition for a country."""

    type: str
    label: str
    level: int
    default_model: str | None = None
    forecast_models: list[ForecastModel] = []


class CountryDetail(BaseModel):
    """Full capability manifest for a country — region types, models, and generation sources."""

    country: str
    region_id: UUID
    name: str
    capacity_kW: float
    centroid: Centroid
    region_types: list[RegionType] = []
    generation_sources: list[GenerationSource] = []


class RegionSummary(BaseModel):
    """Summary of a region (nation, DNO, GSP, etc.)."""

    id: UUID
    name: str
    type: str | None = None
    capacity_kW: float
    centroid: Centroid


class RegionDetail(RegionSummary):
    """Detailed region information including metadata."""

    metadata: dict[str, str | int | float] = Field(default_factory=dict)


class ForecastValue(BaseModel):
    """A single forecast value at a point in time."""

    time: dt.datetime
    power_kW: float
    plevels_kW: dict[str, float] = Field(default_factory=dict)


class ForecastResponse(BaseModel):
    """Forecast time series for a region, with shared metadata."""

    capacity_kW: float
    model_name: str | None = None
    model_version: str | None = None
    created_time: dt.datetime | None = None
    init_time: dt.datetime | None = None
    values: list[ForecastValue]


class GenerationValue(BaseModel):
    """A single observed generation value at a point in time."""

    time: dt.datetime
    power_kW: float


class GenerationResponse(BaseModel):
    """Observed generation time series for a region, with shared metadata."""

    capacity_kW: float
    observer_name: str | None = None
    values: list[GenerationValue]


class RegionForecastValue(BaseModel):
    """A single forecast value for one region — used in snapshot responses."""

    region_id: UUID
    capacity_kW: float
    power_kW: float
    plevels_kW: dict[str, float] = Field(default_factory=dict)


class ForecastSnapshot(BaseModel):
    """Snapshot forecast across all regions at a single point in time."""

    time: dt.datetime
    model_name: str | None = None
    model_version: str | None = None
    created_time: dt.datetime | None = None
    init_time: dt.datetime | None = None
    values: list[RegionForecastValue]


class RegionGenerationValue(BaseModel):
    """A single observed generation value for one region — used in snapshot responses."""

    region_id: UUID
    capacity_kW: float
    power_kW: float


class GenerationSnapshot(BaseModel):
    """Snapshot observed generation across all regions at a single point in time."""

    time: dt.datetime
    observer_name: str | None = None
    values: list[RegionGenerationValue]


class RegionForecast(BaseModel):
    """Forecast time series for one region — used in matrix responses."""

    region_id: UUID
    capacity_kW: float
    power_kW: list[float]
    plevels_kW: dict[str, list[float]] = Field(default_factory=dict)


class RegionForecastMatrix(BaseModel):
    """Forecast time series for all regions across a time window."""

    model_name: str | None = None
    model_version: str | None = None
    created_time: dt.datetime | None = None
    init_time: dt.datetime | None = None
    times: list[dt.datetime]
    regions: list[RegionForecast]


class RegionGeneration(BaseModel):
    """Generation time series for one region — used in matrix responses."""

    region_id: UUID
    capacity_kW: float
    power_kW: list[float]


class RegionGenerationMatrix(BaseModel):
    """Observed generation time series for all regions across a time window."""

    observer_name: str | None = None
    times: list[dt.datetime]
    regions: list[RegionGeneration]
