"""Defines the domain interface for interacting with a backend."""

import abc
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException

from .endpoint_types import (
    ActualPower,
    ForecastHorizon,
    PredictedPower,
    Site,
    SiteProperties,
    Substation,
    SubstationProperties,
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
    async def save_api_call_to_db(self, url: str, authdata: dict[str, str]) -> None:
        """Saves an API call to the database."""
        pass

    @abc.abstractmethod
    async def get_sites(self, authdata: dict[str, str]) -> list[Site]:
        """Get a list of sites."""
        pass

    @abc.abstractmethod
    async def put_site(
        self,
        site_uuid: UUID,
        site_properties: SiteProperties,
        authdata: dict[str, str],
    ) -> Site:
        """Update site info."""
        pass

    @abc.abstractmethod
    async def get_site_forecast(
        self,
        site_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[PredictedPower]:
        """Get a forecast for a site."""
        pass

    @abc.abstractmethod
    async def get_site_generation(
        self, site_uuid: UUID, authdata: dict[str, str],
    ) -> list[ActualPower]:
        """Get the generation for a site."""
        pass

    @abc.abstractmethod
    async def post_site_generation(
        self, site_uuid: UUID, generation: list[ActualPower], authdata: dict[str, str],
    ) -> None:
        """Post the generation for a site."""
        pass

    @abc.abstractmethod
    async def get_substations(self, authdata: dict[str, str]) -> list[Substation]:
        """Get a list of substations."""
        pass

    @abc.abstractmethod
    async def get_substation_forecast(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
    ) -> list[PredictedPower]:
        """Get forecasted generation values of a substation."""
        pass

    @abc.abstractmethod
    async def get_substation(
        self,
        location_uuid: UUID,
        authdata: dict[str, str],
    ) -> SubstationProperties:
        """Get substation metadata."""
        pass

def get_db_client() -> DatabaseInterface:
    """Get the client implementation.

    Note: This should be overridden via FastAPI's dependency injection system with an actual
    database client implementation.
    """
    raise HTTPException(
        status_code=401,
        detail="No database client implementation has been provided.",
    )

DBClientDependency = Annotated[DatabaseInterface, Depends(get_db_client)]
