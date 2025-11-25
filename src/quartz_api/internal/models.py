"""Defines the application models and interfaces."""

import abc
import datetime as dt
from enum import Enum

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

    def to_timezone(self, tz: dt.timezone) -> "PredictedPower":
        """Converts the time of this predicted power value to the given timezone."""
        return PredictedPower(
            PowerKW=self.PowerKW,
            Time=self.Time.astimezone(tz=tz),
            CreatedTime=self.CreatedTime.astimezone(tz=tz),
        )


class ActualPower(BaseModel):
    """Defines the data structure for an actual power value returned by the API."""

    PowerKW: float
    Time: dt.datetime

    def to_timezone(self, tz: dt.timezone) -> "ActualPower":
        """Converts the time of this predicted power value to the given timezone."""
        return ActualPower(
            PowerKW=self.PowerKW,
            Time=self.Time.astimezone(tz=tz),
        )


class SiteProperties(BaseModel):
    """Site metadata."""

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
    latitude: float | None = Field(
        None,
        json_schema_extra={"description": "The site's latitude"},
        ge=-90,
        le=90,
    )
    longitude: float | None = Field(
        None,
        json_schema_extra={"description": "The site's longitude"},
        ge=-180,
        le=180,
    )
    capacity_kw: float | None = Field(
        None,
        json_schema_extra={"description": "The site's total capacity in kw"},
        ge=0,
    )


class Site(BaseModel):
    """Site metadata with site_uuid."""

    site_uuid: str = Field(..., json_schema_extra={"description": "The site uuid assigned by ocf."})
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


class DatabaseInterface(abc.ABC):
    """Defines the interface for a generic database connection."""

    @abc.abstractmethod
    async def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[PredictedPower]:
        """Returns a list of predicted solar power production for a given location.

        Args:
            location: The location for which to fetch predicted power.
            forecast_horizon: The forecast horizon to use.
            forecast_horizon_minutes: The forecast horizon in minutes to use.
            smooth_flag: Whether to smooth the forecast data.
        """
        pass

    @abc.abstractmethod
    async def get_actual_solar_power_production_for_location(
        self,
        location: str,
    ) -> list[ActualPower]:
        """Returns a list of actual solar power production for a given location."""
        pass

    @abc.abstractmethod
    async def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: int | None = None,
        smooth_flag: bool = True,
    ) -> list[PredictedPower]:
        """Returns a list of predicted wind power production for a given location."""
        pass

    @abc.abstractmethod
    async def get_actual_wind_power_production_for_location(
        self,
        location: str,
    ) -> list[ActualPower]:
        """Returns a list of actual wind power production for a given location."""
        pass

    @abc.abstractmethod
    async def get_wind_regions(self) -> list[str]:
        """Returns a list of wind regions."""
        pass

    @abc.abstractmethod
    async def get_solar_regions(self) -> list[str]:
        """Returns a list of solar regions."""
        pass

    @abc.abstractmethod
    async def save_api_call_to_db(self, url: str, user: str | None = None) -> None:
        """Saves an API call to the database."""
        pass

    @abc.abstractmethod
    async def get_sites(self, authdata: dict[str, str]) -> list[Site]:
        """Get a list of sites."""
        pass

    @abc.abstractmethod
    async def put_site(
        self,
        site_uuid: str,
        site_properties: SiteProperties,
        authdata: dict[str, str],
    ) -> Site:
        """Update site info."""
        pass

    @abc.abstractmethod
    async def get_site_forecast(
        self,
        site_uuid: str,
        authdata: dict[str, str],
    ) -> list[PredictedPower]:
        """Get a forecast for a site."""
        pass

    @abc.abstractmethod
    async def get_site_generation(
        self, site_uuid: str, authdata: dict[str, str],
    ) -> list[ActualPower]:
        """Get the generation for a site."""
        pass

    @abc.abstractmethod
    async def post_site_generation(
        self, site_uuid: str, generation: list[ActualPower], authdata: dict[str, str],
    ) -> None:
        """Post the generation for a site."""
        pass
