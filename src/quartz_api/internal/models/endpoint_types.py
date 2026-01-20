"""Defines the domain models for the application."""

import datetime as dt
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends
from pydantic import BaseModel, Field


def convert_to_camelcase(snake_str: str) -> str:
    """Converts a given snake_case string into camelCase."""
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


class EnhancedBaseModel(BaseModel):
    """Ensures that attribute names are returned in camelCase."""

    # Automatically creates camelcase alias for field names
    # See https://pydantic-docs.helpmanual.io/usage/model_config/#alias-generator
    class Config:  # noqa: D106
        alias_generator = convert_to_camelcase
        # allow_population_by_field_name = True
        # orm_mode = True
        # underscore_attrs_are_private = True
        from_attributes = True
        populate_by_name = True



class ForecastHorizon(str, Enum):
    """Defines the forecast horizon options.

    Can either be
    - latest: Gets the latest forecast values.
    - horizon: Gets the forecast values for a specific horizon.
    - day_ahead: Gets the day ahead forecast values.
    """

    latest = "latest"
    horizon = "horizon"
    day_ahead = "day_ahead"


class PredictedPower(BaseModel):
    """Defines the data structure for a predicted power value returned by the API."""

    power_kW: float
    time: dt.datetime
    created_time: dt.datetime = Field(exclude=True)
    forecaster_version: str = Field(exclude=True, default="not-set")
    forecaster_name: str = Field(exclude=True, default="not-set")
    plevel_kW: dict[str, float]  = Field(
        {},
        description="A dictionary of probabilistic levels for the forecast. "
        "Keys are the level names (e.g., 'p10', 'p50', 'p90'), "
        "and values are the corresponding power values in kW.",
    )

    def to_timezone(self, tz: str) -> "PredictedPower":
        """Converts the time of this predicted power value to the given timezone."""
        return PredictedPower(
            power_kW=self.power_kW,
            time=self.time.astimezone(tz=ZoneInfo(key=tz)),
            created_time=self.created_time.astimezone(tz=ZoneInfo(key=tz)),
        )

class ActualPower(BaseModel):
    """Defines the data structure for an actual power value returned by the API."""

    PowerKW: float
    Time: dt.datetime

    def to_timezone(self, tz: str) -> "ActualPower":
        """Converts the time of this predicted power value to the given timezone."""
        return ActualPower(
            PowerKW=self.PowerKW,
            Time=self.Time.astimezone(tz=ZoneInfo(key=tz)),
        )

class LocationPropertiesBase(BaseModel):
    """Properties common to all locations."""

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
    metadata: dict[str, str|int|dict] = Field(
        {},
        json_schema_extra={"description": "Metadata associated with the location"},
    )

class SiteProperties(LocationPropertiesBase):
    """Properties specific to a site."""

    client_site_name: str | None = Field(
        None,
        json_schema_extra={"description": "The name of the site as given by the providing user."},
    )
    orientation: float | None = Field(
        180,
        json_schema_extra={
            "description": "The rotation of the panel in degrees. 180° points south",
        },
    )
    tilt: float | None = Field(
        35,
        json_schema_extra={
            "description": "The tile of the panel in degrees. 90° indicates the panel is vertical.",
        },
    )

class Site(SiteProperties):
    """Site information, including properties and unique identifier."""

    site_uuid: UUID = Field(
        ...,
        json_schema_extra={"description": "The unique identifier for the site."},
    )


class Region(BaseModel):
    """Region metadata."""

    region_name: str = Field(..., json_schema_extra={"description": "The name of the region."})
    region_metadata: dict | None = Field(
        None,
        json_schema_extra={"description": "Additional metadata about the region."},
    )


class SubstationProperties(LocationPropertiesBase):
    """Properties specific to a substation."""

    substation_name: str | None = Field(
        None,
        json_schema_extra={"description": "The name of the substation."},
    )
    substation_type : Literal["primary", "secondary"] = Field(
        ...,
        json_schema_extra={"description": "The type of the substation."},
    )

class Substation(SubstationProperties):
    """Substation information, including properties and unique identifier."""

    substation_uuid: UUID = Field(
        ...,
        json_schema_extra={"description": "The unique identifier for the substation."},
    )

class OneDatetimeManyForecastValues(BaseModel):
    """One datetime with many forecast values."""

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the forecast")
    forecast_values_kW: dict[int|str, float] = Field(
        ...,
        description="List of forecasts by ids. Key is forecast id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small.",
    )


def get_timezone() -> str:
    """Stub function for timezone dependency.

    Note: This should be overidden in the router to provide the actual timezone.
    """
    return "UTC"


class OneDatetimeManyForecastValuesMW(EnhancedBaseModel):
    """One datetime with many forecast values.

    This is a legacy route that is being phased out.
    """

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the gsp yield")
    forecast_values: dict[int|str, float] = Field(
        ...,
        description="List of forecasts by ids. Key is gsp_id, value is generation_mw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )

class GSPYieldGroupByDatetime(EnhancedBaseModel):
    """gsp yields for one a singel datetime.

    This is a legacy route that is being phased out.
    """

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the gsp yield")
    generation_kw_by_gsp_id: dict[int|str, float] = Field(
        ...,
        description="List of generations by ids. Key is gsp_id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )


TZDependency = Annotated[str, Depends(get_timezone)]
