"""Domain models and interfaces for the application."""

from .db_interface import (
    EnergyType,
    LocationType,
    StorageInterface,
    StorageClientDependency,
    get_storage_client,
    EnergyType,
    LocationType,
    PredictedGenerationValue,
    ActualGenerationValue,
    Location,
)
from .endpoint_types import (
    TZDependency,
    get_timezone,
    UTCDatetime,
    UTCDatetimeDefaultWindowEnd,
    UTCDatetimeDefaultNowWindowStart,
    UTCDatetimeDefaultWindowStart,
    UTCDatetimeNonAware,
    UTCDatetimeDefaultWindowEndNonAware,
    ForecastHorizon,
)


