"""Pydantic models for v1 API request/response types."""

import datetime as dt
from typing import Annotated
from uuid import UUID

from fastapi import Path, Query
from pydantic import BaseModel, Field

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
        pattern="^(pvlive_in_day|pvlive_day_after)$",
        examples=["pvlive_in_day", "pvlive_day_after"],
    ),
]

class Source(BaseModel):
    """An available forecast source (energy type)."""

    name: str
    label: str


class RegionType(BaseModel):
    """A region type definition for a country."""

    type: str
    label: str
    level: int


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

    target_time: dt.datetime
    power_kW: float
    capacity_kW: float
    created_time: dt.datetime | None = None
    forecaster_name: str | None = None
    forecaster_version: str | None = None
    plevels_kW: dict[str, float] = Field(default_factory=dict)


class GenerationValue(BaseModel):
    """A single observed generation value at a point in time."""

    time: dt.datetime
    power_kW: float
    capacity_kW: float
