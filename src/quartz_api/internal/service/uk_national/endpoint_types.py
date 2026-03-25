"""Classes defining types for router endpoints."""

import datetime as dt
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator
from typing_extensions import override

from quartz_api.internal import models

# Feels like this could be put in the below map
model_names_external_to_internal = {
    "blend": "blend",
    "pvnet_intraday": "pvnet_v2",
    "pvnet_day_ahead": "pvnet_day_ahead",
    "pvnet_intraday_ecmwf_only": "pvnet_ecmwf",
    "pvnet_intraday_met_office_only": "pvnet_ukv_only",
    "pvnet_intraday_sat_only": "pvnet_sat_only",
}

class ModelName(StrEnum):
    """Available model options for national forecasts."""

    blend = "blend"
    pvnet_intraday = "pvnet_intraday"
    pvnet_day_ahead = "pvnet_day_ahead"
    pvnet_intraday_ecmwf_only = "pvnet_intraday_ecmwf_only"
    pvnet_intraday_met_office_only = "pvnet_intraday_met_office_only"
    pvnet_intraday_sat_only = "pvnet_intraday_sat_only"


def convert_to_camelcase(snake_str: str) -> str:
    """Converts a given snake_case string into camelCase."""
    first, *others = snake_str.split("_")
    return "".join([first.lower(), *map(str.title, others)])


def convert_list_of_gsp_ids(gsp_ids:str | None) -> list[int] | None:
    """Convert a comma-separated string of GSP IDs to a list of integers."""
    if gsp_ids is None:
        return gsp_ids

    return [int(gsp_id) for gsp_id in gsp_ids.split(",")]

class EnhancedBaseModel(BaseModel):
    """Ensures that attribute names are returned in camelCase."""

    # Automatically creates camelcase alias for field names
    # See https://pydantic-docs.helpmanual.io/usage/model_config/#alias-generator
    class Config:  # noqa: D106
        alias_generator = convert_to_camelcase
        from_attributes = True
        populate_by_name = True

class GSPYieldGroupByDatetime(EnhancedBaseModel):
    """gsp yields for one a singel datetime.

    This is a legacy route that is being phased out.
    """

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the gsp yield")
    generation_kw_by_gsp_id: dict[int | str, float] = Field(
        ...,
        description="List of generations by ids. Key is gsp_id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )


class OneDatetimeManyForecastValuesMW(EnhancedBaseModel):
    """One datetime with many forecast values.

    This is a legacy route that is being phased out.
    """

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the gsp yield")
    forecast_values: dict[int | str, float] = Field(
        ...,
        description="List of forecasts by ids. Key is gsp_id, value is generation_mw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )


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
    def from_location(cls, loc: models.Location) -> "Location":
        """Change internal Location to endpoint Location.

        RegionSQL is defined in nowcasting_datamodel
        """
        region_gsp_id = int(loc.metadata["gsp_id"])
        installed_capacity_mw = loc.capacity_kilowatts / 10**3
        if "capacity_no_degradation_kw" in loc.metadata:
            installed_capacity_mw = loc.metadata["capacity_no_degradation_kw"] / 10**3
        full_name = loc.metadata.get("full_name", loc.name)
        gsp_name = loc.name
        gsp_group = loc.metadata.get("gsp_group", gsp_name)
        region_name = full_name

        return Location(
            label=f"GSP_{region_gsp_id}",
            gsp_id=region_gsp_id,
            gsp_name=gsp_name.upper(),
            gsp_group=gsp_group.upper(),
            region_name=region_name,
            installed_capacity_mw=installed_capacity_mw,
        )

    @override
    def model_post_init(self, __context: Any) -> None:
        # Another hack to ensure the national locations matches the old datamodel-backed API.
        if self.gsp_id == 0:
            self.label = "National-GB"
            self.gsp_name = "National"
            self.gsp_group = "National"
            self.region_name = " National" # Yes, the space was there before...


class MLModel(EnhancedBaseModel):
    """ML model that is being used."""

    name: str | None = Field(..., description="The name of the model")
    version: str | None = Field(..., description="The version of the model")

    @override
    def model_post_init(self, __context: Any) -> None:
        # Neither the external model name map, nore the Model enum, accurately captures
        # the names returned by the old datamodel-backed API. So I hack them in here.
        if self.name is not None:
            self.name = self.name.rsplit("_adjust")[0]
            rename_map: dict[str, str] = {
                "pvnet_sat_only": "pvnet-sat-only",
                "pvnet_ukv_only": "pvnet-ukv-only",
            }
            if self.name in rename_map:
                self.name = rename_map[self.name]


class ForecastValue(EnhancedBaseModel):
    """One Forecast of generation at one timestamp."""

    target_time: dt.datetime = Field(
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

    gsp: dt.datetime = Field(..., description="The time when the input GSP data was last updated")
    nwp: dt.datetime = Field(..., description="The time when the input NWP data was last updated")
    pv: dt.datetime = Field(..., description="The time when the input PV data was last updated")
    satellite: dt.datetime = Field(
        ...,
        description="The time when the input satellite data was last updated",
    )


class Forecast(EnhancedBaseModel):
    """A single Forecast."""

    location: Location = Field(..., description="The location object for this forecaster")
    model: MLModel = Field(..., description="The name of the model that made this forecast")
    forecast_creation_time: dt.datetime | None = Field(
        None,
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

    initialization_datetime_utc: dt.datetime | None = Field(
        None,
        description="The time when the forecast should be initialized",
        exclude=True,
    )


class GSPYield(EnhancedBaseModel):
    """GSP Yield data."""

    datetime_utc: dt.datetime = Field(..., description="The timestamp of the gsp yield")
    solar_generation_kw: float = Field(..., description="The amount of solar generation")

    @field_validator("solar_generation_kw")
    def result_check(cls, v: float) -> float:
        """Round to 2 decimal places."""
        return round(v, 2)


class LocationWithGSPYields(Location):
    """Location object with GSPYields."""

    gsp_yields: list[GSPYield] = Field(default_factory=list, description="List of gsp yields")

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
        default_factory=dict,
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

    forecast_values: list[NationalForecastValue] = Field(
        default_factory=list,
        description="List of forecast values",
    )


class Status(EnhancedBaseModel):
    """Status Model for a single message."""

    status: str = Field(..., description="Status description")
    message: str = Field(..., description="Status Message")


# This gets populated by the data platform storage backend at API startup.
gsp_id_map: dict[int, models.Location] = {}
