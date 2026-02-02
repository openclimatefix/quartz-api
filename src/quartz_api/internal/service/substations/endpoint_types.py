"""Types for the endpoints served by the substations router."""

import datetime as dt
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class SubstationProperties(BaseModel):
    """Properties specific to a substation."""

    latitude: float = Field(
        ...,
        json_schema_extra={"description": "The location's latitude"},
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        ...,
        json_schema_extra={"description": "The location's longitude"},
        ge=-180,
        le=180,
    )
    capacity_kW: float = Field(
        ...,
        json_schema_extra={"description": "The location's total capacity in kw"},
        ge=0,
    )
    metadata: dict[str, str | int | dict] = Field(
        {},
        json_schema_extra={"description": "Metadata associated with the location"},
    )

    substation_name: str | None = Field(
        None,
        json_schema_extra={"description": "The name of the substation."},
    )
    substation_type: Literal["primary", "secondary"] = Field(
        ...,
        json_schema_extra={"description": "The type of the substation."},
    )


class Substation(SubstationProperties):
    """Substation information, including properties and unique identifier."""

    substation_uuid: UUID = Field(
        ...,
        json_schema_extra={"description": "The unique identifier for the substation."},
    )


class PredictedPower(BaseModel):
    """Defines the data structure for a predicted power value returned by the API."""

    power_kW: float
    time: AwareDatetime
    created_time: AwareDatetime | None = Field(None, exclude=True)
    initialization_timestamp_utc: AwareDatetime | None = Field(
        None,
        description="The timestamp (UTC) when the forecast was initialized.",
    )
    forecaster_version: str = Field(exclude=True, default="not-set")
    forecaster_name: str = Field(exclude=True, default="not-set")
    plevel_kW: dict[str, float] = Field(
        {},
        description="A dictionary of probabilistic levels for the forecast. "
        "Keys are the level names (e.g., 'p10', 'p50', 'p90'), "
        "and values are the corresponding power values in kW.",
    )


class OneDatetimeManyForecastValues(BaseModel):
    """One datetime with many forecast values."""

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the forecast")
    forecast_values_kW: dict[int | str, float] = Field(
        ...,
        description="List of forecasts by ids. Key is forecast id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small.",
    )
