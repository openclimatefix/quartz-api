"""Defines the domain interface for interacting with a backend."""

import abc
import dataclasses
import datetime as dt
from enum import Enum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException


class EnergyType(Enum):
    """Enum for different types of energy generation."""

    SOLAR = 1
    WIND = 2


class LocationType(Enum):
    """Enum for different types of locations."""

    SITE = 1
    SUBSTATION = 2
    GSP = 3
    REGION = 4
    NATION = 5
    DNO = 6


@dataclasses.dataclass(slots=True)
class PredictedGenerationValue:
    """Predicted generation value with additional metadata."""

    power_kilowatts: float
    valid_timestamp: dt.datetime
    location_uuid: UUID
    capacity_kilowatts: float

    forecaster_name: str
    forecaster_version: str
    created_timestamp: dt.datetime | None = None
    init_timestamp: dt.datetime | None = None
    """Dictionary of probabilistic levels for the forecast.

    Keys are the level names (e.g., 'p10', 'p50', 'p90'),
    and values are the corresponding power values in kW."""
    plevels_kilowatts: dict[str, float] = dataclasses.field(default_factory=dict)

    # metadata: Additional metadata about the forecast
    metadata: dict[str, str | float] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True)
class ActualGenerationValue:
    """Generation value recorded by an observer."""

    power_kilowatts: float
    valid_timestamp: dt.datetime
    location_uuid: UUID
    capacity_kilowatts: float

    observer_name: str


@dataclasses.dataclass(slots=True)
class Location:
    """A location that has tracked or forecasted generation data."""

    uuid: UUID
    name: str
    latitude: float
    longitude: float
    capacity_kilowatts: float
    location_type: LocationType | None = None
    energy_type: EnergyType | None = None
    metadata: dict[str, str | int | float] = dataclasses.field(default_factory=dict)


class StorageInterface(abc.ABC):
    """Defines the interface for a generic storage system."""

    @abc.abstractmethod
    async def get_predicted_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: EnergyType,
        location_type: LocationType,
        authdata: dict[str, str],
        created_cutoff: dt.datetime | None = None,
        forecast_horizon_minutes: int = 0,
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
        day_ahead: bool = False,
        day_ahead_closure_time_local: dt.time | None = None,
    ) -> list[PredictedGenerationValue]:
        """Return a list of predicted power values for a given location and time window.

        The location_uuid parameter can also be a string to support legacy quartzdb routes that
        expect a name, not a UUID.
        """
        pass

    @abc.abstractmethod
    async def put_predicted_generation(
        self,
        generation_values: list[PredictedGenerationValue],
        location_type: LocationType,
        energy_type: EnergyType,
        authdata: dict[str, str],
    ) -> None:
        """Store predicted generation values in the storage system."""
        pass

    @abc.abstractmethod
    async def get_actual_generation(
        self,
        location_uuid: UUID | str,
        window_start: dt.datetime,
        window_end: dt.datetime,
        energy_type: EnergyType,
        location_type: LocationType,
        authdata: dict[str, str],
        observer_name: str | None = None,
        created_cutoff: dt.datetime | None = None,
    ) -> list[ActualGenerationValue]:
        """Return a list of predicted power values for a given location and time window.

        The location_uuid parameter can also be a string to support legacy quartzdb routes that
        expect a name, not a UUID.
        """
        pass

    @abc.abstractmethod
    async def put_actual_generation(
        self,
        generation_values: list[ActualGenerationValue],
        energy_type: EnergyType,
        location_type: LocationType,
        authdata: dict[str, str],
    ) -> None:
        """Store actual generation values in the storage system."""
        pass

    @abc.abstractmethod
    async def get_predicted_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: EnergyType,
        authdata: dict[str, str],
        forecaster_name: str | None = None,
        forecaster_version: str | None = None,
    ) -> list[PredictedGenerationValue]:
        """Return forecasted generation values for multiple locations at a given timestamp."""
        pass

    @abc.abstractmethod
    async def get_actual_generation_snapshot(
        self,
        location_uuids: list[UUID],
        snapshot_timestamp_utc: dt.datetime,
        energy_type: EnergyType,
        authdata: dict[str, str],
        observer_name: str | None = None,
    ) -> list[ActualGenerationValue]:
        """Return actual generation values for multiple locations at a given timestamp."""
        pass

    @abc.abstractmethod
    async def get_locations(
        self,
        energy_type: EnergyType | None,
        location_type: LocationType | None,
        authdata: dict[str, str],
        location_uuid: UUID | None = None,
        enclosing_location_uuid: UUID | None = None,
        location_names: list[str] | None = None,
    ) -> list[Location]:
        """Return a list of locations for a given energy and location type.

        If energy_type is None, locations of all energy types are returned.
        If location_type is None, locations of all types are returned.
        If enclosing_location_uuid is provided, only locations enclosed by that
        location (i.e. children/descendants) are returned.
        If location_names is provided, only locations with those names are returned.
        """
        pass

    @abc.abstractmethod
    async def put_location(
        self,
        location: Location,
        location_type: LocationType,
        energy_type: EnergyType,
        authdata: dict[str, str],
    ) -> Location:
        """Store or update a location in the storage system."""
        pass

    @abc.abstractmethod
    async def log_api_call(
        self,
        url: str,
        authdata: dict[str, str],
    ) -> None:
        """Log an API call to the storage system."""
        pass


def get_storage_client() -> StorageInterface:
    """Get the storage client implementation.

    Note: This should be overridden via FastAPI's dependency injection system with an actual
    storage client implementation.
    """
    raise HTTPException(
        status_code=401,
        detail="No storage client implementation has been provided.",
    )


StorageClientDependency = Annotated[StorageInterface, Depends(get_storage_client)]
