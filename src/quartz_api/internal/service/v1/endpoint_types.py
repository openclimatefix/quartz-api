"""Pydantic models for v1 API request/response types."""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import Path, Query
from pydantic import BaseModel, Field

from .country_config import COUNTRIES


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


ValidSource = Annotated[
    str,
    Path(
        description="The energy source type.",
        pattern="^(wind|solar)$",
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
    forecast_models: list[ForecastModel] = []


class RegionSummary(BaseModel):
    """Summary of a region (nation, DNO, GSP, etc.)."""

    id: UUID
    name: str
    type: str | None = None
    capacity_kW: float
    latitude: float
    longitude: float


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
    values: list[RegionForecastValue]
