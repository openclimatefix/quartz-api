"""pydantic models for API.

The models are
- ForecastValue: one forecast value at one timestamp
- Forecast: a full forecast for a GSP including metadata
- GSPYield: one truth value at one timestamp
- GSPYieldGroupByDatetime: gsp yields for one a singel datetime
- Location: information about the GSP
- LocationWithGSPYields: Location with list of GSPYields
- InputDataLastUpdated: information about when the input data was last updated
- MLModel: information about the ML model used to create the forecast
- NationalYield: GSPYield for national forecast
- NationalForecastValue: ForecastValue for national forecast with properties
- NationalForecast: Forecast for national forecast
- OneDatetimeManyForecastValues: one datetime with many forecast values
- Status: status message for the API

"""

import logging
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from quartz_api.internal.models import (
    Region, 
    GSPYieldGroupByDatetime as GSPYieldGroupByDatetimeDefault, 
    OneDatetimeManyForecastValuesMW as OneDatetimeManyForecastValuesMWDefault
)

logger = logging.getLogger(__name__)


def convert_to_camelcase(snake_str: str) -> str:
    """Converts a given snake_case string into camelCase."""
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


class EnhancedBaseModel(BaseModel):
    """Ensures that attribute names are returned in camelCase."""

    # Automatically creates camelcase alias for field names
    # See https://pydantic-docs.helpmanual.io/usage/model_config/#alias-generator
    class Config:  # noqa: D106
        alias_generator = convert_to_camelcase
        from_attributes = True
        populate_by_name = True


class GSPYieldGroupByDatetime(GSPYieldGroupByDatetimeDefault, EnhancedBaseModel):
    """ gsp yields for one a singel datetime, using CamelCase"""

class OneDatetimeManyForecastValuesMW(OneDatetimeManyForecastValuesMWDefault, EnhancedBaseModel):
    """ One datetime with many forecast values, using CamelCase"""  

class Location(EnhancedBaseModel):
    """Location that the forecast is for."""

    label: str = Field(..., description="")
    gsp_id: int | None = Field(None, description="The Grid Supply Point (GSP) id")
    gsp_name: str | None = Field(None, description="The GSP name")
    gsp_group: str | None = Field(None, description="The GSP group name")
    region_name: str | None = Field(None, description="The GSP region name")
    installed_capacity_mw: float | None = Field(
        None,
        description="The installed capacity of the GSP in MW",
    )

    @classmethod
    def from_region(cls, region: Region) -> "Location":
        """Change RegionSQL to Location.

        RegionSQL is defined in nowcasting_datamodel
        """
        region_gsp_id = int(region.region_metadata["gsp_id"])
        installed_capacity_mw = region.region_metadata["effective_capacity_watts"] / 10**6
        if "full_name" in region.region_metadata:
            full_name = region.region_metadata["full_name"]
        else:
            full_name = region.region_name

        gsp_name = region.region_name
        gsp_group = region.region_name
        region_name = full_name

        return Location(
            label=f"GSP_{region_gsp_id}",
            gsp_id=region_gsp_id,
            gsp_name=gsp_name.upper(),
            gsp_group=gsp_group.upper(),
            region_name=region_name,
            installed_capacity_mw=installed_capacity_mw,
        )



class MLModel(EnhancedBaseModel):
    """ML model that is being used."""

    name: str | None = Field(..., description="The name of the model")
    version: str | None = Field(..., description="The version of the model")


class ForecastValue(EnhancedBaseModel):
    """One Forecast of generation at one timestamp."""

    target_time: datetime = Field(
        ...,
        description=(
            "The target time for which the forecast is produced, indicating the period end time "
            "(e.g., a target_time of 12:30 refers to the period from 12:00 to 12:30)."
        ),
    )
    expected_power_generation_megawatts: float = Field(
        ...,
        ge=0,
        description="The forecasted value in MW",
    )

    expected_power_generation_normalized: float | None = Field(
        None,
        ge=0,
        description="The forecasted value divided by the GSP capacity [%]",
    )


class InputDataLastUpdated(EnhancedBaseModel):
    """Information about the input data that was used to create the forecast."""

    gsp: datetime = Field(..., description="The time when the input GSP data was last updated")
    nwp: datetime = Field(..., description="The time when the input NWP data was last updated")
    pv: datetime = Field(..., description="The time when the input PV data was last updated")
    satellite: datetime = Field(
        ...,
        description="The time when the input satellite data was last updated",
    )


class Forecast(EnhancedBaseModel):
    """A single Forecast."""

    location: Location = Field(..., description="The location object for this forecaster")
    model: MLModel = Field(..., description="The name of the model that made this forecast")
    forecast_creation_time: datetime = Field(
        ...,
        description="The time when the forecaster was made",
    )
    historic: bool = Field(
        False,
        description="if False, the forecast is just the latest forecast. "
        "If True, historic values are also given",
    )
    forecast_values: list[ForecastValue] = Field(
        ...,
        description="List of forecasted value objects. Each value has the datestamp and a value",
    )
    input_data_last_updated: InputDataLastUpdated = Field(
        ...,
        description="Information about the input data that was used to create the forecast",
    )

    initialization_datetime_utc: datetime | None = Field(
        None,
        description="The time when the forecast should be initialized",
        exclude=True,
    )


class GSPYield(EnhancedBaseModel):
    """GSP Yield data."""

    datetime_utc: datetime = Field(..., description="The timestamp of the gsp yield")
    solar_generation_kw: float = Field(..., description="The amount of solar generation")

    @field_validator("solar_generation_kw")
    def result_check(cls, v: float) -> float:
        """Round to 2 decimal places."""
        return round(v, 2)


class LocationWithGSPYields(Location):
    """Location object with GSPYields."""

    gsp_yields: list[GSPYield] | None = Field([], description="List of gsp yields")

    def from_location_sql(self) -> "LocationWithGSPYields":
        """Change LocationWithGSPYieldsSQL to LocationWithGSPYields.

        LocationWithGSPYieldsSQL is defined in nowcasting_datamodel
        """
        return LocationWithGSPYields(
            label=self.label,
            gsp_id=self.gsp_id,
            gsp_name=self.gsp_name,
            gsp_group=self.gsp_group,
            region_name=self.region_name,
            installed_capacity_mw=self.installed_capacity_mw,
            gsp_yields=[
                GSPYield(
                    datetime_utc=gsp_yield.datetime_utc,
                    solar_generation_kw=gsp_yield.solar_generation_kw,
                )
                for gsp_yield in self.gsp_yields
            ],
        )


NationalYield = GSPYield


class NationalForecastValue(ForecastValue):
    """One Forecast of generation at one timestamp include properties."""

    plevels: dict = Field(
        None,
        description="Dictionary to hold properties of the forecast, like p_levels. ",
    )

    expected_power_generation_normalized: float | None = Field(
        None,
        ge=0,
        description="exclude the normalized power",
        exclude=True,
    )

    @field_validator("expected_power_generation_megawatts")
    def result_check(cls, v: float) -> float:
        """Round to 2 decimal places."""
        return round(v, 2)


class NationalForecast(Forecast):
    """One Forecast of generation at one timestamp."""

    forecast_values: list[NationalForecastValue] = Field(..., description="List of forecast values")


class Status(EnhancedBaseModel):
    """Status Model for a single message."""

    status: str = Field(..., description="Status description")
    message: str = Field(..., description="Status Message")
