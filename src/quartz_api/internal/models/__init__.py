"""Domain models and interfaces for the application."""

from .db_interface import (
    DatabaseInterface,
    DBClientDependency,
    get_db_client,
)
from .endpoint_types import (
    ActualPower,
    ForecastHorizon,
    PredictedPower,
    Region,
    ForecastMetadata,
    GSPYieldGroupByDatetime,
    OneDatetimeManyForecastValues,
    SiteProperties,
    Site,
    SubstationProperties,
    Substation,
    TZDependency,
    get_timezone,
)


