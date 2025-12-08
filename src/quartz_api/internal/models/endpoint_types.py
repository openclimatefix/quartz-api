"""Defines the domain models for the application."""

import datetime as dt
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends
from pydantic import BaseModel, Field


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

    PowerKW: float
    Time: dt.datetime
    CreatedTime: dt.datetime = Field(exclude=True)

    def to_timezone(self, tz: str) -> "PredictedPower":
        """Converts the time of this predicted power value to the given timezone."""
        return PredictedPower(
            PowerKW=self.PowerKW,
            Time=self.Time.astimezone(tz=ZoneInfo(key=tz)),
            CreatedTime=self.CreatedTime.astimezone(tz=ZoneInfo(key=tz)),
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
    capacity_kw: float = Field(
        ...,
        json_schema_extra={"description": "The location's total capacity in kw"},
        ge=0,
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


def get_timezone() -> str:
    """Stub function for timezone dependency.

    Note: This should be overidden in the router to provide the actual timezone.
    """
    return "UTC"

TZDependency = Annotated[str, Depends(get_timezone)]
