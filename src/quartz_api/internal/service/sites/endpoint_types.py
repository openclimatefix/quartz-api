"""Endpoint classes for the sites router."""

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator


class SiteProperties(BaseModel):
    """Properties specific to a site."""

    latitude: float | None = Field(
        None,
        description="The location's latitude",
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        None,
        description="The location's longitude",
        ge=-180,
        le=180,
    )
    capacity_kw: float | None = Field(
        None,
        description="The location's total capacity in kw",
        ge=0,
    )
    client_site_name: str | None = Field(
        None,
        description="The name of the site as given by the providing user.",
    )
    orientation: float | None = Field(
        None,
        description="The rotation of the panel in degrees. 180° points south",
    )
    tilt: float | None = Field(
        None,
        description="The tile of the panel in degrees. 90° indicates the panel is vertical.",
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

    site_uuid: UUID = Field(
        ...,
        description="The site uuid assigned by ocf.",
    )
    latitude: float = Field(
        ...,
        description="The site's latitude",
        ge=-90,
        le=90,
    )
    longitude: float = Field(
        ...,
        description="The site's longitude",
        ge=-180,
        le=180,
    )
    capacity_kw: float = Field(
        ...,
        description="The site's total capacity in kw",
        ge=0,
    )
    orientation: float | None = Field(
        180,
        description="The rotation of the panel in degrees. 180° points south",
    )
    tilt: float | None = Field(
        35,
        description="The tile of the panel in degrees. 90° indicates the panel is vertical.",
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
