"""Defines the application models and interfaces."""

import abc
import datetime as dt
from typing import List, Optional

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
    """Site metadata"""

    client_site_name: Optional[str] = Field(
        None,
        json_schema_extra={"description": "The name of the site as given by the providing user."},
    )
    orientation: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "The rotation of the panel in degrees. 180° points south"
        },
    )
    tilt: Optional[float] = Field(
        None,
        json_schema_extra={
            "description": "The tile of the panel in degrees. 90° indicates the panel is vertical."
        },
    )
    latitude: Optional[float] = Field(
        None, json_schema_extra={"description": "The site's latitude"}, ge=-90, le=90
    )
    longitude: Optional[float] = Field(
        None, json_schema_extra={"description": "The site's longitude"}, ge=-180, le=180
    )
    capacity_kw: Optional[float] = Field(
        None, json_schema_extra={"description": "The site's total capacity in kw"}, ge=0
    )


class Site(BaseModel):
    """Site metadata with site_uuid"""

    site_uuid: str = Field(..., json_schema_extra={"description": "The site uuid assigned by ocf."})
    client_site_name: Optional[str] = Field(
        None,
        json_schema_extra={"description": "The name of the site as given by the providing user."},
    )
    orientation: Optional[float] = Field(
        180,
        json_schema_extra={
            "description": "The rotation of the panel in degrees. 180° points south"
        },
    )
    tilt: Optional[float] = Field(
        35,
        json_schema_extra={
            "description": "The tile of the panel in degrees. 90° indicates the panel is vertical."
        },
    )
    latitude: float = Field(
        ..., json_schema_extra={"description": "The site's latitude"}, ge=-90, le=90
    )
    longitude: float = Field(
        ..., json_schema_extra={"description": "The site's longitude"}, ge=-180, le=180
    )
    capacity_kw: float = Field(
        ..., json_schema_extra={"description": "The site's total capacity in kw"}, ge=0
    )


class DatabaseInterface(abc.ABC):
    """Defines the interface for a generic database connection."""

    @abc.abstractmethod
    def get_predicted_solar_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
        smooth_flag: bool = True,
    ) -> list[PredictedPower]:
        """Returns a list of predicted solar power production for a given location."""
        pass

    @abc.abstractmethod
    def get_actual_solar_power_production_for_location(self, location: str) -> list[ActualPower]:
        """Returns a list of actual solar power production for a given location."""
        pass

    @abc.abstractmethod
    def get_predicted_wind_power_production_for_location(
        self,
        location: str,
        forecast_horizon: ForecastHorizon = ForecastHorizon.latest,
        forecast_horizon_minutes: Optional[int] = None,
        smooth_flag: bool = True,
    ) -> list[PredictedPower]:
        """Returns a list of predicted wind power production for a given location."""
        pass

    @abc.abstractmethod
    def get_actual_wind_power_production_for_location(self, location: str) -> list[ActualPower]:
        """Returns a list of actual wind power production for a given location."""
        pass

    @abc.abstractmethod
    def get_wind_regions(self) -> list[str]:
        """Returns a list of wind regions."""
        pass

    @abc.abstractmethod
    def get_solar_regions(self) -> list[str]:
        """Returns a list of solar regions."""
        pass

    @abc.abstractmethod
    def save_api_call_to_db(self, url: str, user=None):
        """Saves an API call to the database."""
        pass

    @abc.abstractmethod
    def get_sites(self, email: str) -> list[Site]:
        """Get a list of sites"""
        pass

    @abc.abstractmethod
    def put_site(self, site_uuid: str, site_properties: SiteProperties, email:str) -> Site:
        """Update site info"""
        pass

    @abc.abstractmethod
    def get_site_forecast(self, site_uuid: str, email:str) -> list[PredictedPower]:
        """Get a forecast for a site"""
        pass

    @abc.abstractmethod
    def get_site_generation(self, site_uuid: str, email:str) -> list[ActualPower]:
        """Get the generation for a site"""
        pass

    @abc.abstractmethod
    def post_site_generation(self, site_uuid: str, generation: list[ActualPower], email:str) -> None:
        """Post the generation for a site"""
        pass
