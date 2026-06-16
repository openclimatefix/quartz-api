"""Pydantic models definining the router's request/response types."""

import datetime as dt
from typing import Annotated

from fastapi import Path
from pydantic import BaseModel, Field


class ActualPower(BaseModel):
    """Defines the data structure for an actual power value returned by the API."""

    PowerKW: float
    Time: (
        dt.datetime
    )  # TODO: Use AwareDatetime from Pydantic and remove to_timezone conversion
    location_uuid: str = Field("not-set", exclude=True)


class PredictedPower(BaseModel):
    """Defines the data structure for a predicted power value returned by the API."""

    PowerKW: float
    Time: dt.datetime
    created_time: dt.datetime | None = Field(None, exclude=True)
    initialization_timestamp_utc: dt.datetime | None = Field(
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


ValidSource = Annotated[
    str,
    Path(
        description="The source of the generation or forecast data.",
        pattern="^(solar)$",
    ),
]


class GetSourcesResponse(BaseModel):
    """Model for the sources endpoint response."""

    sources: list[str]


class GetRegionsResponse(BaseModel):
    """Model for the regions endpoint response."""

    regions: list[str]


class GetHistoricGenerationResponse(BaseModel):
    """Model for the historic generation endpoint response."""

    values: list[ActualPower]


class GetForecastGenerationResponse(BaseModel):
    """Model for the forecast generation endpoint response."""

    values: list[PredictedPower]
