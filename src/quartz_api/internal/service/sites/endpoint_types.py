"""Enpoint classes for the sites router."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class SiteProperties(BaseModel):
    """Properties specific to a site."""

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


class ActualPower(BaseModel):
    """Defines the data structure for an actual power value returned by the API."""

    PowerKW: float
    Time: AwareDatetime
    location_uuid: str = Field("not-set", exclude=True)
