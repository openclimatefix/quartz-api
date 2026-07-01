"""Endpoint classes for the sites router."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class SiteProperties(BaseModel):
    """Properties specific to a site."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "client_site_name": "string",
                "orientation": 0,
                "tilt": 0,
                "latitude": -90,
                "longitude": -180,
                "capacity_kw": 0,
            },
        },
    }

    latitude: float | None = Field(
        None,
        json_schema_extra={"description": "The location's latitude"},
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        None,
        json_schema_extra={"description": "The location's longitude"},
        ge=-180,
        le=180,
    )
    capacity_kw: float | None = Field(
        None,
        json_schema_extra={"description": "The location's total capacity in kw"},
        ge=0,
    )
    client_site_name: str | None = Field(
        None,
        json_schema_extra={"description": "The name of the site as given by the providing user."},
    )
    orientation: float | None = Field(
        None,
        json_schema_extra={
            "description": "The rotation of the panel in degrees. 180° points south",
        },
    )
    tilt: float | None = Field(
        None,
        json_schema_extra={
            "description": "The tile of the panel in degrees. 90° indicates the panel is vertical.",
        },
    )

    @field_validator("latitude")
    @classmethod
    def round_latitude(cls, v: float | None) -> float | None:
        """Round latitude to 4 decimal places for presentation."""
        if v is not None:
            return round(v, 4)
        return None

    @field_validator("longitude")
    @classmethod
    def round_longitude(cls, v: float | None) -> float | None:
        """Round longitude to 4 decimal places for presentation."""
        if v is not None:
            return round(v, 4)
        return None


class Site(SiteProperties):
    """Site information, including properties and unique identifier."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "site_uuid": "string",
                "client_site_name": "string",
                "orientation": 180,
                "tilt": 35,
                "latitude": -90,
                "longitude": -180,
                "capacity_kw": 0,
            },
        },
    }

    site_uuid: UUID = Field(
        ...,
        json_schema_extra={"description": "The site uuid assigned by ocf."},
    )
    latitude: float = Field(
        ...,
        json_schema_extra={"description": "The site's latitude"},
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        ...,
        json_schema_extra={"description": "The site's longitude"},
        ge=-180,
        le=180,
    )
    capacity_kw: float = Field(
        ...,
        json_schema_extra={"description": "The site's total capacity in kw"},
        ge=0,
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


class SitePredictedPower(BaseModel):
    """Defines the data structure for a predicted power value returned by the API."""

    model_config = {"title": "PredictedPower"}

    PowerKW: float
    Time: AwareDatetime
    created_time: AwareDatetime | None = Field(None, exclude=True)
    forecaster_version: str = Field(exclude=True, default="not-set")
    forecaster_name: str = Field(exclude=True, default="not-set")


class SiteActualPower(BaseModel):
    """Defines the data structure for an actual power value returned by the API."""

    model_config = {"title": "ActualPower"}

    PowerKW: float
    Time: AwareDatetime
