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
        # allow_population_by_field_name = True
        # orm_mode = True
        # underscore_attrs_are_private = True
        from_attributes = True
        populate_by_name = True


class Location(EnhancedBaseModel):
    """Location that the forecast is for."""

    label: str = Field(..., description="")
    gsp_id: int | None = Field(None, description="The Grid Supply Point (GSP) id", index=True)
    gsp_name: str | None = Field(None, description="The GSP name")
    gsp_group: str | None = Field(None, description="The GSP group name")
    region_name: str | None = Field(None, description="The GSP region name")
    installed_capacity_mw: float | None = Field(
        None, description="The installed capacity of the GSP in MW",
    )


class MLModel(EnhancedBaseModel):
    """ML model that is being used."""

    name: str | None = Field(..., description="The name of the model", index=True)
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
        ..., ge=0, description="The forecasted value in MW",
    )

    expected_power_generation_normalized: float | None = Field(
        None, ge=0, description="The forecasted value divided by the GSP capacity [%]",
    )


class InputDataLastUpdated(EnhancedBaseModel):
    """Information about the input data that was used to create the forecast."""

    gsp: datetime = Field(..., description="The time when the input GSP data was last updated")
    nwp: datetime = Field(..., description="The time when the input NWP data was last updated")
    pv: datetime = Field(..., description="The time when the input PV data was last updated")
    satellite: datetime = Field(
        ..., description="The time when the input satellite data was last updated",
    )


class Forecast(EnhancedBaseModel):
    """A single Forecast."""

    location: Location = Field(..., description="The location object for this forecaster")
    model: MLModel = Field(..., description="The name of the model that made this forecast")
    forecast_creation_time: datetime = Field(
        ..., description="The time when the forecaster was made",
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


class GSPYieldGroupByDatetime(EnhancedBaseModel):
    """gsp yields for one a singel datetime."""

    datetime_utc: datetime = Field(..., description="The timestamp of the gsp yield")
    generation_kw_by_gsp_id: dict[int, float] = Field(
        ...,
        description="List of generations by gsp_id. Key is gsp_id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )


class OneDatetimeManyForecastValues(EnhancedBaseModel):
    """One datetime with many forecast values."""

    datetime_utc: datetime = Field(..., description="The timestamp of the gsp yield")
    forecast_values: dict[int, float] = Field(
        ...,
        description="List of forecasts by gsp_id. Key is gsp_id, value is generation_kw. "
        "We keep this as a dictionary to keep the size of the file small ",
    )


def convert_forecasts_to_many_datetime_many_generation(
    forecasts: list[Forecast],
    historic: bool = True,
    start_datetime_utc: datetime | None = None,
    end_datetime_utc: datetime | None = None,
) -> list[OneDatetimeManyForecastValues]:
    """Change forecasts to list of OneDatetimeManyForecastValues.

    This converts a list of forecast objects to a list of OneDatetimeManyForecastValues objects.

    N forecasts, which T forecast values each,
    is converted into
    T OneDatetimeManyForecastValues objects with N forecast values each.

    This reduces the size of the object as the datetimes are not repeated for each forecast values.
    """
    many_forecast_values_by_datetime = {}

    # loop over locations and gsp yields to create a dictionary of gsp generation by datetime
    for forecast in forecasts:
        gsp_id = str(forecast.location.gsp_id)
        forecast_values = forecast.forecast_values_latest if historic else forecast.forecast_values

        for forecast_value in forecast_values:
            datetime_utc = forecast_value.target_time
            if start_datetime_utc is not None and datetime_utc < start_datetime_utc:
                continue
            if end_datetime_utc is not None and datetime_utc > end_datetime_utc:
                continue

            forecast_mw = forecast_value.expected_power_generation_megawatts
            forecast_mw = round(forecast_mw, 2)

            # if the datetime object is not in the dictionary, add it
            if datetime_utc not in many_forecast_values_by_datetime:
                many_forecast_values_by_datetime[datetime_utc] = {gsp_id: forecast_mw}
            else:
                many_forecast_values_by_datetime[datetime_utc][gsp_id] = forecast_mw

    # convert dictionary to list of OneDatetimeManyForecastValues objects
    many_forecast_values = []
    for datetime_utc, forecast_values in many_forecast_values_by_datetime.items():
        many_forecast_values.append(
            OneDatetimeManyForecastValues(
                datetime_utc=datetime_utc, forecast_values=forecast_values,
            ),
        )

    return many_forecast_values


NationalYield = GSPYield


class NationalForecastValue(ForecastValue):
    """One Forecast of generation at one timestamp include properties."""

    plevels: dict = Field(
        None, description="Dictionary to hold properties of the forecast, like p_levels. ",
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
